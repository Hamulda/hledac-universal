"""Tier-aware feed scheduler for bounded sprint runs.

Scheduler over SprintLifecycleManager. Executes sprint cycles dispatched
by sprint owner (core.__main__.run_sprint). All report truth flows
through the owner, not the scheduler.

Tier priority (high→low): surface → structured_ti → deep → archive → other

Invariants enforced:
  Winddown: no new work after lifecycle signals WINDUP.
  Dedup: same entry_hash never processed twice in one sprint.
  Lifecycle: authoritative for time and phase transitions.
  Export: always runs on teardown, including zero-signal exits.
  Concurrency: TaskGroup for owned tasks; no background threads.
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import logging
import struct
import time as _time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Generic, Protocol, TypeVar, cast

from runtime.logging_setup import get_logger
from runtime.context.bounded_dicts import (
    BoundedLRUDict,
    DEFAULT_ENTRIES_PER_SOURCE_MAXSIZE,
    DEFAULT_FEED_ACCEPTED_MAXSIZE,
    DEFAULT_FETCH_LATENCY_EMA_MAXSIZE,
    DEFAULT_NOVELTY_BONUSES_MAXSIZE,
    DEFAULT_SEEN_HASHES_MAXSIZE,
    DEFAULT_SOURCE_WEIGHTS_MAXSIZE,
)

T = TypeVar("T")
import uuid
from datetime import UTC
from enum import Enum, auto

import msgspec

from hledac.universal.utils.async_helpers import safe_create_task, safe_wait_for
from core.result import try_op

if TYPE_CHECKING:
    from hledac.universal.knowledge.graph_service import GraphService


class GraphServiceLifecycle(Protocol):
    """Explicit lifecycle Protocol for GraphService — replaces weakref to global."""

    async def acquire(self) -> GraphService:
        """Acquire GraphService instance for this sprint."""
        ...

    async def release(self, gs: GraphService) -> None:
        """Release GraphService and cleanup resources after sprint."""
        ...


class ResourceLease(Generic[T]):
    """Explicit, deterministic resource lease. No weakref magic.

    Replaces _SprintCleanupHandle + weakref.finalize pattern.

    Invariants:
      - No weakref — lifecycle is fully explicit and deterministic
      - Context manager protocol for deterministic cleanup
      - Thread-safe: cleanup runs on the calling thread, not GC thread

    M1 8GB: No GC pressure from weakref scanning.
    """

    __slots__ = ("_obj", "_token", "_registry", "_released")

    def __init__(self, obj: T, registry: "ResourceRegistry", token: str) -> None:
        self._obj = obj
        self._token = token
        self._registry = registry
        self._released = False

    @property
    def obj(self) -> T:
        if self._released:
            raise RuntimeError(f"Resource {self._token} already released")
        return self._obj

    def release(self) -> None:
        """Release resource — idempotent, safe to call multiple times."""
        if not self._released:
            self._registry._release(self._token)
            self._released = True

    def __enter__(self) -> T:
        return self.obj

    def __exit__(self, *exc: object) -> None:
        self.release()


class ResourceRegistry:
    """Bounded resource registry with explicit lifecycle. No weakref.

    Replaces ResourceLifecycleRegistry (WeakValueDictionary + dict + deque).
    Single dict[str, Any] — simpler, faster, M1 8GB friendly.

    Invariants:
      - Always-on: no feature flags
      - Bounded: max_size prevents unbounded growth
      - Fail-safe: cleanup errors are suppressed, logged via try_op
    """

    __slots__ = ("_resources", "_cleanups", "_max_size", "_tokens")

    def __init__(self, max_size: int = 16) -> None:
        self._resources: dict[str, Any] = {}
        self._cleanups: dict[str, Callable[[], None]] = {}
        self._max_size = max_size
        self._tokens: deque[str] = deque(maxlen=max_size)

    def acquire(self, obj: Any, cleanup: Callable[[], None] | None = None) -> ResourceLease:
        """Acquire a resource lease. Auto-evicts oldest if at capacity."""
        while len(self._tokens) >= self._max_size:
            old = self._tokens.popleft()
            self._release(old)
        token = str(uuid.uuid4())[:8]
        self._resources[token] = obj
        if cleanup:
            self._cleanups[token] = cleanup
        self._tokens.append(token)
        return ResourceLease(obj, self, token)

    def _release(self, token: str) -> None:
        """Internal release — called by ResourceLease.release() or evict."""
        cleanup = self._cleanups.pop(token, None)
        if cleanup:
            try_op(cleanup, label=f"cleanup_{token}")
        self._resources.pop(token, None)
        try:
            self._tokens.remove(token)
        except ValueError:
            pass

    def release(self, token: str) -> None:
        """Public release API — idempotent."""
        self._release(token)


_graph_service_registry: ResourceRegistry | None = None


def _get_graph_service_registry() -> ResourceRegistry:
    """Lazy initialization — avoids import-order issues."""
    global _graph_service_registry
    if _graph_service_registry is None:
        _graph_service_registry = ResourceRegistry(max_size=16)
    return _graph_service_registry


class _SprintCleanupHandle:
    """Explicit cleanup handle — no weakref.finalize.

    Replaces weakref.finalize pattern with explicit cleanup() call.
    Deterministic: cleanup runs on the calling thread, not GC thread.

    Usage:
        handle = _SprintCleanupHandle(sprint_scheduler_instance)
        try:
            # sprint work
        finally:
            handle.cleanup()  # explicit, deterministic, no GC dependency
    """

    __slots__ = ("_obj", "_sentinel_cb", "_cleanup_called")

    def __init__(self, obj: Any, sentinel_cb: Callable[[str, dict], None] | None = None) -> None:
        self._obj = obj
        self._sentinel_cb = sentinel_cb
        self._cleanup_called = False

    def cleanup(self) -> None:
        """Explicit cleanup — deterministic, no GC dependency. Idempotent."""
        if self._cleanup_called:
            return
        self._cleanup_called = True
        if self._sentinel_cb is not None:
            try:
                self._sentinel_cb("cleanup", {"source": "explicit", "obj": self._obj})
            except Exception:  # noqa: BLE001 — best-effort; callback handler; non-critical
                pass


from core.env_config import ENV
from hledac.universal.knowledge.graph_service import _DEFAULT_GRAPH_SERVICE
from hledac.universal.layers.ghost_layer import StagnationError
from hledac.universal.monitoring.alert_manager import check_zero_findings_alert, get_memory_delta_tracker
from hledac.universal.runtime.sprint_timer import SprintTimer
from hledac.universal.utils.async_helpers import parallel, safe_create_task, safe_gather_ok
from hledac.universal.utils.batch_dns import get_batch_dns_resolver

# OTEL instrumentation — strict import with fallback chain
try:
    from otel._instrumentation import instrumented as _otel_instrumented
except ImportError:
    from hledac.universal.otel._instrumentation import instrumented as _otel_instrumented

# IntCounterLayout — strict import from runtime (Rust extension)
try:
    from runtime.int_counter_layout import IntCounterLayout, IntCounterLayoutRust, batch_compute_scores
except ImportError:
    try:
        from hledac.universal.runtime.int_counter_layout import (
            IntCounterLayout,
            IntCounterLayoutRust,
            batch_compute_scores,
        )
    except ImportError:
        IntCounterLayout: type | None = None
        IntCounterLayoutRust: type | None = None
        batch_compute_scores: Any = None
import msgspec

from core.psutil_shim import psutil as _psutil

# orjson — strict import with fallback
try:
    import orjson as _orjson_mod
    HAS_ORJSON: bool = True
except ImportError:
    _orjson_mod = None  # type: ignore[assignment]
    HAS_ORJSON: bool = False
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode

_NONFEED_DIAGNOSTIC_FALLBACK_LANES = ("CT", "WAYBACK", "PASSIVE_DNS", "PIVOT_EXECUTOR", "DOH")


class _Sentinel:
    __slots__ = ()

    def __repr__(self):
        return "<unset>"


_UNSET = _Sentinel()


def _safe_getattr(obj: Any, attr: str, default: Any = _UNSET) -> Any:
    """Get attribute without lambda allocation. Returns default if missing.

    For bool attrs (is_closed, is_frozen): coerces to bool.
    For callable attrs (is_closed()): calls if present, returns default if missing.
    M1 8GB: no heap allocation per call.
    """
    try:
        val = getattr(obj, attr)
    except AttributeError:
        return default
    try:
        result = val()
    except TypeError:
        return val
    except (AttributeError, TypeError):
        return default
    return result


def canonical_lane_name(lane: object) -> str:
    """Normalize lane to UPPERCASE string -- handles Enum values and plain strings."""
    value = getattr(lane, "value", lane)
    return str(value).upper()


from collections import deque

_ADVISORY_LOG_LRU_MAX = 16


class _AdvisoryLogLRU(msgspec.Struct, gc=False):
    """FIFO advisory dedup: dict for O(1) counts, deque for O(1) insertion order.

    No-promote on hit: deque order is never modified on cache hit — only on insert/evict.
    """

    counts: dict[str, int]
    order: deque[str]


_advisory_log_lru_var: contextvars.ContextVar[_AdvisoryLogLRU] = contextvars.ContextVar(
    "_advisory_log_lru", default=_AdvisoryLogLRU(counts={}, order=deque(maxlen=_ADVISORY_LOG_LRU_MAX))
)
_advisory_log_suppressed_total_var: contextvars.ContextVar[int] = contextvars.ContextVar(
    "_advisory_log_suppressed_total", default=0
)


def _make_seen_hashes() -> BoundedLRUDict:
    return BoundedLRUDict(maxsize=DEFAULT_SEEN_HASHES_MAXSIZE)


def _make_entries_per_source() -> BoundedLRUDict:
    return BoundedLRUDict(maxsize=DEFAULT_ENTRIES_PER_SOURCE_MAXSIZE)


def _make_hits_per_source() -> BoundedLRUDict:
    return BoundedLRUDict(maxsize=DEFAULT_ENTRIES_PER_SOURCE_MAXSIZE)


def _make_source_weights() -> BoundedLRUDict:
    return BoundedLRUDict(maxsize=DEFAULT_SOURCE_WEIGHTS_MAXSIZE)


def _make_novelty_bonuses() -> BoundedLRUDict:
    return BoundedLRUDict(maxsize=DEFAULT_NOVELTY_BONUSES_MAXSIZE)


def _make_feed_accepted() -> BoundedLRUDict:
    return BoundedLRUDict(maxsize=DEFAULT_FEED_ACCEPTED_MAXSIZE)


def _make_fetch_latency_ema() -> BoundedLRUDict:
    return BoundedLRUDict(maxsize=DEFAULT_FETCH_LATENCY_EMA_MAXSIZE)


class SprintRunContext(msgspec.Struct, gc=False):
    """Per-sprint mutable state — replaces instance dicts in SprintScheduler.

    Accessed via get_sprint_ctx() for copy-on-write isolation between
    concurrent sprints or async tasks.

    ISSUE-3 FIX: Memory bounds
      - recent_iocs: bounded to 200 entries via collections.deque(maxlen=200)
        (replaces unbounded list[dict] which leaked on 18h sprints)
      - seen_hashes: BoundedLRUDict(maxsize=100_000) — LRU eviction with drop counter
      - entries_per_source / hits_per_source: BoundedLRUDict(maxsize=500) each
      - source_weights: BoundedLRUDict(maxsize=500)
      - novelty_bonuses: BoundedLRUDict(maxsize=10_000)
      - feed_accepted_per_source: BoundedLRUDict(maxsize=500)
      - fetch_latency_ema: BoundedLRUDict(maxsize=200)
      - arrow_batch: bounded HARD_CAP=50 000 with oldest eviction on flush failure
      - pivot_rewards: bounded at usage site via history[-20:] slice
      - pivot_stats: replaced per reset, not a BoundedLRUDict (tiny dict)

    All BoundedLRUDict fields use LRU eviction — least-recently-used entry
    is silently evicted when capacity is reached. Drop counters in
    SprintSchedulerResult track evictions for telemetry.
    """

    seen_hashes: BoundedLRUDict = msgspec.field(default_factory=_make_seen_hashes)
    entries_per_source: BoundedLRUDict = msgspec.field(default_factory=_make_entries_per_source)
    hits_per_source: BoundedLRUDict = msgspec.field(default_factory=_make_hits_per_source)
    source_weights: BoundedLRUDict = msgspec.field(default_factory=_make_source_weights)
    novelty_bonuses: BoundedLRUDict = msgspec.field(default_factory=_make_novelty_bonuses)
    feed_accepted_per_source: BoundedLRUDict = msgspec.field(default_factory=_make_feed_accepted)
    source_economics: dict[str, Any] = msgspec.field(default_factory=dict)
    pivot_stats: dict[str, int] = msgspec.field(default_factory=lambda: {"total": 0, "processed": 0, "errors": 0})
    pivot_rewards: dict[str, list[float]] = msgspec.field(default_factory=dict)
    recent_iocs: deque[dict] = msgspec.field(default_factory=lambda: deque(maxlen=200))
    fetch_latency_ema: BoundedLRUDict = msgspec.field(default_factory=_make_fetch_latency_ema)
    arrow_batch: list[dict] = msgspec.field(default_factory=list)
    result: Any = msgspec.field(default=None)


_sprint_run_ctx: contextvars.ContextVar[SprintRunContext | None] = contextvars.ContextVar(
    "_sprint_run_ctx", default=None
)


def get_sprint_ctx() -> SprintRunContext:
    """Get current sprint context. Raise if not established."""
    ctx = _sprint_run_ctx.get()
    if ctx is None:
        raise RuntimeError("SprintRunContext not established — call within sprint run()")
    return ctx


def reset_sprint_ctx() -> None:
    """Reset context (call between sprints / for testing)."""
    _sprint_run_ctx.set(None)


def _reset_advisory_log_dedup() -> None:
    """Clear the LRU dedup state. Call between test runs or sprint cycles."""
    _advisory_log_lru_var.set(_AdvisoryLogLRU(counts={}, order=deque(maxlen=_ADVISORY_LOG_LRU_MAX)))
    _advisory_log_suppressed_total_var.set(0)


def _log_advisory_dedup(log: Any, msg_key: str, *args: Any, **kwargs: Any) -> bool:
    """Emit a warning at most once per unique msg_key within a 16-slot FIFO window.

    Returns True if the message was emitted, False if it was suppressed
    (caller can use this to short-circuit expensive arg construction).

    Bounded:
      - _ADVISORY_LOG_LRU_MAX = 16 unique keys
      - FIFO eviction when full (oldest key dropped, NOT promoted on hit)

    ISSUE-041 fix: plain dict + deque replaces deprecated OrderedDict.
    HIT:  O(1) membership test + counter increment only — deque order unchanged.
    MISS: O(1) dict setitem + deque.append + optional deque.popleft for FIFO.

    Usage:
        _log_advisory_dedup(log, f"dht_sidecar_fail:{type(e).__name__}",
                            "[F214Q] DHT sidecar failed: %s", e)
    """
    key = str(msg_key)
    lru = _advisory_log_lru_var.get()
    if key in lru.counts:
        lru.counts[key] += 1
        _advisory_log_suppressed_total_var.set(_advisory_log_suppressed_total_var.get() + 1)
        return False
    if len(lru.order) >= _ADVISORY_LOG_LRU_MAX:
        evicted_key = lru.order.popleft()
        lru.counts.pop(evicted_key, None)
    lru.counts[key] = 1
    lru.order.append(key)
    log.warning(*args, **kwargs)
    return True


def _advisory_log_stats() -> dict:
    """Snapshot of advisory dedup state for diagnostics/tests."""
    lru = _advisory_log_lru_var.get()
    return {
        "unique_keys": len(lru.counts),
        "max_keys": _ADVISORY_LOG_LRU_MAX,
        "suppressed_total": _advisory_log_suppressed_total_var.get(),
    }


def _build_deep_security_config(sprint_mode: str) -> DeepSecurityConfig:
    """
    DS4: Mode-aware DeepSecurityConfig factory. research=conservative, aggressive=stricter.

    DS1+DS2+DS3 audit fix: use privacy_level to drive the cascade.
    - "medium" activates obfuscation + chaff, but does NOT force heavy crypto.
    - "low" activates audit only, no heavy ops at all.
    - privacy_level="maximum" SILENTLY forces enable_quantum_safe=True +
      enable_steganography=True via _apply_privacy_level() — M1 8GB killer.
      Dead flags (enable_anti_fingerprinting, enable_request_signing,
      enable_zero_knowledge) do not exist in DeepSecurityConfig — Python
      silently ignores them, so they are removed from this factory entirely.
    """
    from hledac.universal.security.deep_research_security import DeepSecurityConfig

    if sprint_mode == "aggressive":
        return DeepSecurityConfig(
            privacy_level="medium",
            enable_quantum_safe=False,
            enable_steganography=False,
            enable_obfuscation=True,
            enable_destruction=True,
            enable_audit=True,
            chaff_enabled=True,
            chaff_ratio=0.2,
            auto_cleanup=True,
        )
    else:
        return DeepSecurityConfig(
            privacy_level="low",
            enable_quantum_safe=False,
            enable_steganography=False,
            enable_obfuscation=False,
            enable_destruction=True,
            enable_audit=True,
            chaff_enabled=False,
            chaff_ratio=0.0,
            auto_cleanup=True,
        )


def resolve_nonfeed_expected_lanes(
    result_expected: tuple[str, ...],
    local_expected: list[str],
    plan_debug_expected: list[str],
    acquisition_profile: str,
) -> tuple[str, ...]:
    """

    Sprint F228F: Resolve effective nonfeed expected lanes with explicit fallback chain.



    Rules:

    - If result_expected is non-empty, use it.

    - Else if local_expected is non-empty, use it.

    - Else if plan_debug_expected is non-empty, use it.

    - Else if acquisition_profile == "nonfeed_diagnostic", return fallback tuple.

    - Never let an empty tuple silently block seed-unlocked lanes.

    """
    if result_expected:
        return result_expected
    if local_expected:
        return tuple(local_expected)
    if plan_debug_expected:
        return tuple(plan_debug_expected)
    if acquisition_profile == "nonfeed_diagnostic":
        return _NONFEED_DIAGNOSTIC_FALLBACK_LANES
    return ()


from hledac.universal.runtime.graph_accumulator import SprintGraphAccumulator
from hledac.universal.utils.async_helpers import safe_gather, safe_gather_fire_and_forget, safe_gather_ok
from hledac.universal.utils.lmdb_bulk import putmulti_bounded

# SourceType — strict import
try:
    from hledac.universal.utils.source_types import SourceType
except ImportError:
    SourceType = None  # type: ignore[assignment,misc]

from hledac.universal.transport.circuit_breaker import (
    _BREAKERS,
    MAX_TRACKED_DOMAINS,
    CBState,
    get_all_breaker_snapshots,
    get_all_breaker_states,
)

_MAX_CHUNK_SIZE: int = 500
_MAX_CHUNK_CONCURRENCY: int = 4  # F320M-R: was 2; M1 8GB has 4P+4E cores, DuckDB writes are I/O-bound
MAX_LANE_REJECTIONS: int = 1000
_gc_sprint_callback_handle: _SprintCleanupHandle | None = None
_gc_callback_registered: bool = False
MAX_GC_STATS: int = 1000
_gc_sprint_stats: deque[dict] = deque(maxlen=MAX_GC_STATS)


def _gc_sprint_callback(phase: str, info: dict) -> None:
    """E4: GC per-collection callback -- records generation and collection counts."""
    _gc_sprint_stats.append({"gen": info.get("generation", -1), "collected": info.get("collected", -1)})


def _gc_sprint_sentinel(_phase: object, _info: object) -> None:
    """E4: Re-registers GC telemetry callback when cleanup() is called at sprint end.

    Called by _SprintCleanupHandle.cleanup() (not by GC itself) to ensure
    _gc_sprint_callback remains registered in gc.callbacks across sprints.
    Telemetry only — does not perform actual cleanup.
    """
    global _gc_callback_registered
    if not _gc_callback_registered and _gc_sprint_callback not in gc.callbacks:
        gc.callbacks.append(_gc_sprint_callback)
        _gc_callback_registered = True


from hledac.universal.knowledge.cross_sprint_memory import get_cross_sprint_memory
from hledac.universal.knowledge.target_memory import (
    MAX_MEMORY_ENTITIES,
    MAX_MEMORY_EXPOSURES,
    MAX_MEMORY_PIVOTS,
    TargetMemoryService,
    TargetMemoryUpdate,
)
from hledac.universal.pipeline.pivot_lane_planner import plan_lanes_for_pivot_seeds
from hledac.universal.runtime.acquisition_strategy import (
    AcquisitionLane,
    AcquisitionLaneOutcome,
    NonfeedMissionController,
    NonfeedPlanDebug,
    NonfeedSeedContext,
    _get_ct_adapter,
    build_lane_query,
    canonicalize_source_family_outcomes,
    get_lane_plan,
    infer_mission_intent,
    is_lane_enabled,
    normalize_source_family_outcome,
    required_terminal_lanes,
    run_enabled_acquisition_lanes_streaming,
    terminality_report,
)
from hledac.universal.runtime.cti.db.duckdb_domain_mv import get_domain_mv
from hledac.universal.runtime.nonfeed_candidate_ledger import (
    NonfeedCandidateLedger,
    extract_domain_candidates_from_text,
)
from hledac.universal.runtime.pivot_planner import generate_pivot_candidates_from_query
from hledac.universal.runtime.shadow_inputs import (
    collect_graph_summary,
    collect_lifecycle_snapshot,
    collect_model_control_facts,
    collect_provider_runtime_facts,
)
from hledac.universal.runtime.shadow_parity import run_shadow_parity
from hledac.universal.runtime.shadow_pre_decision import compose_pre_decision
from hledac.universal.runtime.source_finding_bridge import (
    REJECTION_UNSUPPORTED_SHAPE,
    ct_results_to_findings,
    passive_dns_results_to_findings,
    record_ct_storage_results,
    wayback_results_to_findings,
)
from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner
from hledac.universal.utils.pivot_seed_extractor import extract_pivot_seeds_from_texts

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    from hledac.universal.pipeline.live_feed_pipeline import FeedIngestContext
    from hledac.universal.research_context import ResearchContext
    from hledac.universal.security.deep_research_security import DeepSecurityConfig

    class IntCounterLayoutProto(Protocol):
        """Minimal duck-typed interface for IntCounterLayout (used in hot-path properties)."""

        def get(self, key: str) -> int: ...

        def set(self, key: str, value: int) -> None: ...

        def bump(self, name: str, n: int) -> int: ...


import lmdb
import xxhash

logger = get_logger(__name__)
log = get_logger(__name__)


def _sanitize_debug_text(value: object, *, max_chars: int = 500) -> str:
    """Strip raw HTML/script from debug strings -- do not expose page content."""
    text = str(value or "")
    text = text.replace("<", "‹").replace(">", "›")
    return text[:max_chars]


def _seed_ctx_has_any_items(seed_ctx: Any) -> bool:
    """
    F271D: Single source of truth for "does this seed context have any
    shappable items?" -- duck-typed for NonfeedSeedContext and any other
    object exposing `domains` / `urls` iterables.

    Returns False for None and for contexts with no domains AND no URLs.
    Used by public discovery telemetry to gate `seed_context_available`
    and `bootstrap_eligible` flags. M1 8GB friendly: pure C-speed
    attribute lookup, no Python-level construction.
    """
    if seed_ctx is None:
        return False
    return bool(getattr(seed_ctx, "domains", ()) or getattr(seed_ctx, "urls", ()))


class _PublicStage:
    NOT_SCHEDULED = "NOT_SCHEDULED"
    SCHEDULED = "SCHEDULED"
    BOOTSTRAP_ATTEMPTED = "BOOTSTRAP_ATTEMPTED"
    BOOTSTRAP_ZERO_SUCCESS = "BOOTSTRAP_ZERO_SUCCESS"
    BOOTSTRAP_ACCEPTED = "BOOTSTRAP_ACCEPTED"
    BOOTSTRAP_ATTEMPTED_TIMEOUT = "BOOTSTRAP_ATTEMPTED_TIMEOUT"
    BOOTSTRAP_ZERO_CANDIDATES_TIMEOUT = "BOOTSTRAP_ZERO_CANDIDATES_TIMEOUT"
    DISCOVERY_ATTEMPTED = "DISCOVERY_ATTEMPTED"
    DISCOVERY_ZERO_RESULTS = "DISCOVERY_ZERO_RESULTS"
    DISCOVERY_TIMEOUT = "DISCOVERY_TIMEOUT"
    DISCOVERY_ERROR = "DISCOVERY_ERROR"
    FETCH_ATTEMPTED = "FETCH_ATTEMPTED"
    FETCH_ZERO_SUCCESS = "FETCH_ZERO_SUCCESS"
    FETCH_TIMEOUT = "FETCH_TIMEOUT"
    FETCH_ERROR = "FETCH_ERROR"
    PARSE_ATTEMPTED = "PARSE_ATTEMPTED"
    PARSE_ZERO_TEXT = "PARSE_ZERO_TEXT"
    QUALITY_REJECTED = "QUALITY_REJECTED"
    STORAGE_REJECTED = "STORAGE_REJECTED"
    ACCEPTED = "ACCEPTED"
    TERMINAL = "TERMINAL"


def _compute_public_stage(outcome: dict | None, public_result: Any | None = None) -> tuple[str, dict]:
    """

    Compute public_terminal_stage and public_stage_counters from _public_outcome.



    The stage machine traces the full discovery->fetch->parse->quality->storage

    pipeline to explain why PUBLIC=0.



    Returns (terminal_stage: str, stage_counters: dict).

    """
    if outcome is None:
        return (
            _PublicStage.NOT_SCHEDULED,
            {
                "discovered_urls": 0,
                "fetch_attempted": 0,
                "fetch_success": 0,
                "fetch_timeout": 0,
                "fetch_error": 0,
                "parse_attempted": 0,
                "parse_success": 0,
                "quality_rejected": 0,
                "storage_rejected": 0,
                "accepted_findings": 0,
            },
        )
    error = outcome.get("error", "") or ""
    timeout = outcome.get("timeout", False)
    skip_reason = outcome.get("skip_reason", "") or ""
    raw_count = outcome.get("raw_count", 0) or 0
    built_count = outcome.get("built_count", 0) or 0
    accepted_count = outcome.get("accepted_count", 0) or 0
    attempted = outcome.get("attempted", False)
    skipped = outcome.get("skipped", False)
    if public_result is not None:
        pr = public_result
        _discovered_count = getattr(pr, "discovered", 0) or 0
        _bootstrap_cand = getattr(pr, "public_bootstrap_candidates_count", 0) or 0
        _pub_discovery_raw = getattr(pr, "public_discovery_raw_count", 0) or 0
        if _pub_discovery_raw > 0:
            _raw_count_source = "discovery"
        elif _bootstrap_cand > 0 and _discovered_count == _bootstrap_cand:
            _raw_count_source = "bootstrap"
        elif _bootstrap_cand > 0 and _discovered_count > _bootstrap_cand:
            _raw_count_source = "mixed"
        else:
            _raw_count_source = "unknown"
        stage_counters = {
            "discovered_urls": _discovered_count,
            "raw_count_source": _raw_count_source,
            "fetch_attempted": getattr(pr, "public_fetch_attempted", 0) or 0,
            "fetch_success": getattr(pr, "public_fetch_success", 0) or 0,
            "fetch_timeout": getattr(pr, "public_skipped_timeout", 0) or 0,
            "fetch_error": getattr(pr, "public_skipped_fetch_error", 0) or 0,
            "parse_attempted": getattr(pr, "public_fetch_success", 0) or 0,
            "parse_success": getattr(pr, "public_fetch_success", 0) or 0,
            "quality_rejected": getattr(pr, "public_acceptance_rejected", 0) or 0,
            "storage_rejected": getattr(pr, "public_rejected_storage_rejected", 0) or 0,
            "accepted_findings": getattr(pr, "public_findings_accepted", 0) or 0,
        }
        stage_counters["rejection_reasons"] = list(
            (getattr(pr, "public_acceptance_reject_reasons", None) or {}).items()
        )[:5]
        stage_counters["error_samples"] = list((getattr(pr, "public_skipped_url_sample", None) or ())[:5])
        stage_counters["rejected_url_samples"] = list((getattr(pr, "public_rejected_url_samples", None) or ())[:5])
        stage_counters["bootstrap_candidates"] = getattr(pr, "public_bootstrap_candidates_count", 0) or 0
        stage_counters["bootstrap_fetch_attempted"] = getattr(pr, "public_bootstrap_fetch_attempted", 0) or 0
        stage_counters["bootstrap_fetch_success"] = getattr(pr, "public_bootstrap_fetch_success", 0) or 0
        stage_counters["bootstrap_accepted_findings"] = getattr(pr, "public_bootstrap_accepted_findings", 0) or 0
        stage_counters["bootstrap_errors"] = getattr(pr, "public_bootstrap_errors", 0) or 0
        bootstrap_enabled = getattr(pr, "public_bootstrap_enabled", False)
        psf = getattr(pr, "public_stage_failure", None)
        if bootstrap_enabled and raw_count > 0:
            if accepted_count > 0:
                terminal_stage = _PublicStage.BOOTSTRAP_ACCEPTED
            elif timeout:
                terminal_stage = _PublicStage.BOOTSTRAP_ATTEMPTED_TIMEOUT
            else:
                terminal_stage = _PublicStage.BOOTSTRAP_ZERO_SUCCESS
        elif bootstrap_enabled and raw_count == 0 and timeout:
            terminal_stage = _PublicStage.BOOTSTRAP_ZERO_CANDIDATES_TIMEOUT
        elif psf == "fetch_zero" and accepted_count == 0:
            terminal_stage = _PublicStage.FETCH_ZERO_SUCCESS
        else:
            terminal_stage = _derive_terminal_stage(
                error=error,
                timeout=timeout,
                skip_reason=skip_reason,
                raw_count=raw_count,
                built_count=built_count,
                accepted_count=accepted_count,
                attempted=attempted,
                skipped=skipped,
                public_stage_failure=psf,
            )
    else:
        stage_counters = {
            "discovered_urls": raw_count,
            "fetch_attempted": 0,
            "fetch_success": 0,
            "fetch_timeout": 0,
            "fetch_error": 0,
            "parse_attempted": built_count,
            "parse_success": built_count,
            "quality_rejected": 0,
            "storage_rejected": 0,
            "accepted_findings": accepted_count,
        }
        terminal_stage = _derive_terminal_stage(
            error=error,
            timeout=timeout,
            skip_reason=skip_reason,
            raw_count=raw_count,
            built_count=built_count,
            accepted_count=accepted_count,
            attempted=attempted,
            skipped=skipped,
            public_stage_failure=None,
        )
    return (terminal_stage, stage_counters)


def _derive_terminal_stage(
    error: str,
    timeout: bool,
    skip_reason: str,
    raw_count: int,
    built_count: int,
    accepted_count: int,
    attempted: bool,
    skipped: bool,
    public_stage_failure: str | None,
) -> str:
    """Derive terminal stage from outcome fields."""
    if not attempted:
        return _PublicStage.NOT_SCHEDULED
    if skipped and skip_reason:
        if "remaining_too_low" in skip_reason:
            return _PublicStage.DISCOVERY_TIMEOUT
        return _PublicStage.DISCOVERY_ERROR
    if raw_count == 0:
        if timeout or "timeout" in error.lower() or "timeout" in skip_reason.lower():
            return _PublicStage.DISCOVERY_TIMEOUT
        if error and error not in ("null", ""):
            return _PublicStage.DISCOVERY_ERROR
        return _PublicStage.DISCOVERY_ZERO_RESULTS
    if built_count == 0:
        if timeout or "timeout" in error.lower():
            return _PublicStage.FETCH_TIMEOUT
        if error and error not in ("null", ""):
            return _PublicStage.FETCH_ERROR
        return _PublicStage.FETCH_ZERO_SUCCESS
    if accepted_count == 0:
        if public_stage_failure == "fetch_zero":
            return _PublicStage.FETCH_ZERO_SUCCESS
        if public_stage_failure == "discovery_empty":
            return _PublicStage.DISCOVERY_ZERO_RESULTS
        if built_count > 0:
            return _PublicStage.QUALITY_REJECTED
        return _PublicStage.PARSE_ZERO_TEXT
    if accepted_count > 0:
        return _PublicStage.ACCEPTED
    return _PublicStage.TERMINAL


class _LifecycleAdapter:
    """Adapts any lifecycle object to the runtime/sprint_lifecycle API.

    Normalizes API differences between runtime/ and utils/ versions:
      runtime/sprint_lifecycle: start(), tick(), remaining_time(),
        is_terminal(), should_enter_windup(), _current_phase,
        recommended_tool_mode(), request_abort(), _abort_requested

    Python 3.14 __slots__ optimization:
      - 22 cached-attr slots → 3 slots (_lc, _phase_transition_callback, _prev_phase)
      - __getattr__ for per-instance lazy attr name resolution (<100ns vs ~660ns)
      - No foot-gun: if the underlying object is replaced, delegation breaks
        immediately (no stale cached values), matching the issue description.
      - Phase transitions are preserved (prev_phase tracking + callback).

    Invariants (F320 legacy):
      - __slots__ for M1 8GB RAM savings
      - Fail-safe: AttributeError from _lc propagates as-is
    """

    __slots__ = ("_lc", "_phase_transition_callback", "_prev_phase", "__dict__")

    def __init__(self, lifecycle: Any, phase_transition_callback: Callable[[str, str], None] | None = None) -> None:
        object.__setattr__(self, "_lc", lifecycle)
        object.__setattr__(self, "_phase_transition_callback", phase_transition_callback)
        object.__setattr__(self, "_prev_phase", "BOOT")

    def _notify_phase_transition(self, new_phase: str) -> None:
        """F320: Call phase_transition_callback if phase actually changed."""
        from core.telemetry.context_state import set_sprint_phase as _set_phase

        _set_phase(new_phase)
        callback = object.__getattribute__(self, "_phase_transition_callback")
        if callback is None:
            return
        old = object.__getattribute__(self, "_prev_phase")
        if old == new_phase:
            return
        try:
            callback(old, new_phase)
        except Exception:  # noqa: BLE001 — best-effort; callback handler; non-critical
            pass
        object.__setattr__(self, "_prev_phase", new_phase)

    def _resolve_attr(self, name: str) -> str:
        """Resolve the normalized attr name for `name` on _lc, cached in instance __dict__.

        Falls back through multiple candidate names to handle API differences
        between runtime/ and utils/ lifecycle implementations.
        """
        # Check instance-level cache first (avoids __getattr__ recursion)
        cached: str | None = self.__dict__.get(f"_cached_{name}")
        if cached is not None:
            return cached

        candidates: tuple[str, ...] | str
        if name in (
            "phase_attr",
            "remaining_time_attr",
            "is_terminal_attr",
            "recommended_tool_mode_attr",
            "abort_requested_attr",
            "abort_reason_attr",
            "set_pre_loop_cost_attr",
            "set_windup_lead_attr",
            "set_first_cycle_ran_attr",
            "set_deadline_expired_attr",
            "mark_warmup_done_attr",
            "tick_attr",
        ):
            # Single-candidate attrs
            attr_name = name.replace("_attr", "")
            if hasattr(self._lc, attr_name):
                self.__dict__[f"_cached_{name}"] = attr_name
                return attr_name
            raise AttributeError(f"{type(self._lc).__name__!r} has no attribute {attr_name!r}")

        if name == "start_attr":
            candidates = ("start", "begin_sprint")
        elif name == "should_enter_windup_attr":
            candidates = ("should_enter_windup", "is_windup_phase")
        elif name == "phase_attr_multi":
            candidates = ("_current_phase", "phase", "state", "current_phase")
        else:
            candidates = (name,)

        for candidate in candidates:
            if hasattr(self._lc, candidate):
                self.__dict__[f"_cached_{name}"] = candidate
                return candidate
        raise AttributeError(f"{type(self._lc).__name__!r} has no attribute matching {candidates!r}")

    # ------------------------------------------------------------------
    # Explicit methods for normalization with side-effects / extra logic
    # ------------------------------------------------------------------

    def tick(self, now_monotonic: float | None = None) -> Any:
        """runtime: tick() returns SprintPhase. Fallback: 'UNKNOWN' phase string."""
        try:
            attr_name = self._resolve_attr("tick_attr")
        except AttributeError:
            return "UNKNOWN"
        return getattr(self._lc, attr_name)(now_monotonic)

    def remaining_time(self, now_monotonic: float | None = None) -> float:
        """runtime: remaining_time(). Fallback: 0.0."""
        try:
            attr_name = self._resolve_attr("remaining_time_attr")
        except AttributeError:
            return 0.0
        val = getattr(self._lc, attr_name)
        return float(val() if callable(val) else val)

    def is_terminal(self) -> bool:
        """runtime: is_terminal(). Fallback: _current_phase == TEARDOWN."""
        try:
            attr_name = self._resolve_attr("is_terminal_attr")
        except AttributeError:
            return self._current_phase == "TEARDOWN"
        val = getattr(self._lc, attr_name)
        return bool(val() if callable(val) else val)

    def recommended_tool_mode(self, now_monotonic: float | None = None) -> str:
        """runtime: recommended_tool_mode(). Fallback: 'normal'."""
        try:
            attr_name = self._resolve_attr("recommended_tool_mode_attr")
        except AttributeError:
            return "normal"
        val = getattr(self._lc, attr_name)
        return str(val(now_monotonic) if callable(val) else val)

    def set_pre_loop_cost_s(self, value: float) -> None:
        """F288: Set pre_loop_cost_s on the underlying lifecycle if supported."""
        try:
            attr_name = self._resolve_attr("set_pre_loop_cost_attr")
        except AttributeError:
            return
        setattr(self._lc, attr_name, value)

    def set_windup_lead_s(self, value: float) -> None:
        """O4-FIX: Set windup_lead_s on the underlying lifecycle if supported."""
        try:
            attr_name = self._resolve_attr("set_windup_lead_attr")
        except AttributeError:
            return
        setattr(self._lc, attr_name, value)

    def set_first_cycle_ran(self) -> None:
        """F290: Signal that first acquisition cycle has completed."""
        try:
            attr_name = self._resolve_attr("set_first_cycle_ran_attr")
        except AttributeError:
            return
        val = getattr(self._lc, attr_name)
        if callable(val):
            val()
        else:
            setattr(self._lc, attr_name, True)

    def set_deadline_expired_pre_cycle(self) -> None:
        """F290-Deadline: Signal that hard deadline expired before first cycle."""
        try:
            attr_name = self._resolve_attr("set_deadline_expired_attr")
        except AttributeError:
            return
        getattr(self._lc, attr_name)()

    @property
    def _abort_requested(self) -> bool:
        try:
            attr_name = self._resolve_attr("abort_requested_attr")
        except AttributeError:
            return False
        val = getattr(self._lc, attr_name)
        return bool(val() if callable(val) else val)

    @property
    def _abort_reason(self) -> str:
        try:
            attr_name = self._resolve_attr("abort_reason_attr")
        except AttributeError:
            return ""
        val = getattr(self._lc, attr_name)
        return str(val() if callable(val) else val)

    # ------------------------------------------------------------------
    # __getattr__ for everything else — delegates to _lc with lazy caching
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to _lc with lazy normalization.

        Raises AttributeError if _lc lacks the resolved attribute.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        obj = object.__getattribute__(self, "_lc")

        if name == "_current_phase":
            # Multi-candidate: _current_phase/phase/state/current_phase
            cached: str | None = self.__dict__.get("_cached_phase_attr_multi")
            if cached is not None:
                val = getattr(obj, cached)
            else:
                for candidate in ("_current_phase", "phase", "state", "current_phase"):
                    if hasattr(obj, candidate):
                        self.__dict__["_cached_phase_attr_multi"] = candidate
                        val = getattr(obj, candidate)
                        break
                else:
                    return "UNKNOWN"
            v = val() if callable(val) else val
            return str(v.name if hasattr(v, "name") else v)

        if name == "should_enter_windup":
            # Try should_enter_windup(now), fallback to is_windup_phase()
            cached = self.__dict__.get("_cached_should_enter_windup_attr")
            if cached is not None:
                val = getattr(obj, cached)
                return bool(val() if callable(val) else val)
            for candidate in ("should_enter_windup", "is_windup_phase"):
                if hasattr(obj, candidate):
                    self.__dict__["_cached_should_enter_windup_attr"] = candidate
                    val = getattr(obj, candidate)
                    return bool(val() if callable(val) else val)
            return False

        if name == "start":
            # begin_sprint → start normalization
            cached = self.__dict__.get("_cached_start_attr")
            if cached is not None:
                getattr(obj, cached)()
            elif hasattr(obj, "start"):
                self.__dict__["_cached_start_attr"] = "start"
                obj.start()
            elif hasattr(obj, "begin_sprint"):
                self.__dict__["_cached_start_attr"] = "begin_sprint"
                obj.begin_sprint()
            else:
                raise AttributeError(f"{type(obj).__name__!r} has no start/begin_sprint")
            self._notify_phase_transition("WARMUP")
            return None

        if name == "mark_warmup_done":
            cached = self.__dict__.get("_cached_mark_warmup_done_attr")
            if cached is not None:
                getattr(obj, cached)()
            elif hasattr(obj, "mark_warmup_done"):
                self.__dict__["_cached_mark_warmup_done_attr"] = "mark_warmup_done"
                obj.mark_warmup_done()
            elif hasattr(obj, "transition_to"):
                self.__dict__["_cached_mark_warmup_done_attr"] = "transition_to"
                from hledac.universal.runtime.sprint_lifecycle import SprintPhase
                obj.transition_to(SprintPhase.ACTIVE)
            else:
                raise AttributeError(f"{type(obj).__name__!r} has no mark_warmup_done/transition_to")
            self._notify_phase_transition("ACTIVE")
            return None

        if name == "request_abort":
            cached = self.__dict__.get("_cached_request_abort_attr")
            if cached is not None:
                getattr(obj, cached)()
            elif hasattr(obj, "request_abort"):
                self.__dict__["_cached_request_abort_attr"] = "request_abort"
                obj.request_abort()
            elif hasattr(obj, "_abort_requested"):
                self.__dict__["_cached_request_abort_attr"] = "_abort_requested"
                obj._abort_requested = True
                if hasattr(obj, "_abort_reason"):
                    self.__dict__["_cached_abort_reason_attr"] = "_abort_reason"
                    obj._abort_reason = ""
            else:
                raise AttributeError(f"{type(obj).__name__!r} has no request_abort/_abort_requested")
            return None

        # Generic: attr lookup with callable normalization
        attr_name = self._resolve_attr(name)
        val = getattr(obj, attr_name)
        if callable(val) and not isinstance(val, type):
            # Methods / callable descriptors: invoke them
            return val()
        return val


class SourceTier(Enum):
    """Feed source priority tier."""

    SURFACE = auto()
    STRUCTURED_TI = auto()
    DEEP = auto()
    ARCHIVE = auto()
    OTHER = auto()


_TIER_ORDER = [SourceTier.SURFACE, SourceTier.STRUCTURED_TI, SourceTier.DEEP, SourceTier.ARCHIVE, SourceTier.OTHER]
_DEFAULT_SOURCE_TIER_MAP: dict[str, SourceTier] = {
    "cisa_kev": SourceTier.STRUCTURED_TI,
    "threatfox_ioc": SourceTier.STRUCTURED_TI,
    "urlhaus_recent": SourceTier.STRUCTURED_TI,
    "feodo_ip": SourceTier.STRUCTURED_TI,
    "openphish_feed": SourceTier.STRUCTURED_TI,
}


class CTLossStage(Enum):
    """Enum describing where CT raw evidence is lost in the live bridge path.



    Canonical live path: crtsh_adapter -> ct_results_to_findings -> candidates ->

    duckdb async_ingest -> lane_ct_accepted_findings -> benchmark report.



    Any deviation from this path constitutes a loss stage.

    """

    NO_RAW = "no_raw"
    BRIDGE_NOT_INVOKED = "bridge_not_invoked"
    RAW_NOT_BRIDGED = "raw_not_bridged"
    UNSUPPORTED_RAW_SHAPE = "unsupported_raw_shape"
    ALL_REJECTED_BY_BRIDGE = "all_rejected_by_bridge"
    CANDIDATES_BUILT_NOT_ACCUMULATED = "candidates_built_not_accumulated"
    ACCUMULATED_NOT_STORED = "accumulated_not_stored"
    STORED_NOT_REPORTED = "stored_not_reported"
    NO_LOSS = "no_loss"
    UNKNOWN_LOSS = "unknown_loss"
    PROVIDER_FAILURE = "provider_failure"
    STALE_CACHE_USED = "stale_cache_used"


class SprintSchedulerConfig(msgspec.Struct, frozen=True, gc=False):
    """Configuration for one sprint run."""

    sprint_duration_s: float = 1800.0
    windup_lead_s: float = 180.0
    cycle_sleep_s: float = 5.0
    cycle_budget_s: float = 60.0
    max_cycles: int = 100
    max_parallel_sources: int = 4
    stop_on_first_accepted: bool = False
    export_enabled: bool = True
    export_dir: str = ""
    max_entries_per_cycle: int = 50
    max_hypothesis_depth: int = 3
    max_hypothesis_queries: int = 10
    aggressive_mode: bool = False
    aggressive_branch_timeout_s: float = 45.0
    branch_timeout_budget_s: float = 0.0
    _MAX_BRANCH_TIMEOUT_CAP: float = 300.0
    _MIN_BRANCH_REMAINING_S_DEFAULT: float = 2.0
    _MIN_BRANCH_REMAINING_S_CAP: float = 5.0
    _MIN_BRANCH_REMAINING_S: float = 2.0
    partial_export_findings_interval: int = 10
    source_tier_map: dict[str, SourceTier] = field(default_factory=dict)

    @property
    def effective_windup_lead_s(self) -> float:
        """
        F250 + F272A + F273B + F278A + F285 + F290: Adaptive windup that scales
        with sprint duration. Matches the F221-ABORT pre-flight guard formula exactly.

        F290: Short sprints get smaller windup overhead to avoid consuming 50-100%
        of the sprint budget in windup (F221/F289 abort).
          sprint <= 120s -> 20% ratio (e.g. 60s -> 12s windup, 48s active)
          sprint <= 300s -> 25% ratio (e.g. 300s -> 75s windup, 225s active)
          sprint > 300s  -> 30% ratio (e.g. 600s -> 180s cap, 420s active)
        Clamped [15, 180] to allow short sprints to run without F289 abort.

        F285: Explicit windup_lead_s (non-default 180.0) passes through directly.
        F273B + F288: Aggressive mode → 15% ratio (parallel branches faster).
        """
        if self.windup_lead_s != 180.0:
            raw = float(self.windup_lead_s)
            return float(max(30.0, min(180.0, raw)))
        if self.aggressive_mode:
            ratio = 0.15
        elif self.sprint_duration_s <= 120.0:
            ratio = 0.2
        elif self.sprint_duration_s <= 300.0:
            ratio = 0.25
        else:
            ratio = 0.3
        raw = self.sprint_duration_s * ratio
        return float(max(15.0, min(180.0, raw)))

    @property
    def final_windup_lead_s(self) -> float:
        """
        F290: Adaptive windup for sprint-end synthesis and graceful shutdown.
        Matches effective_windup_lead_s ratio tiers but with [30, 180] floor
        (vs [15, 180] for effective — final needs at least 30s for synthesis).

        F285: Explicit windup_lead_s (non-default 180.0) passes through directly.
        """
        if self.windup_lead_s != 180.0:
            result = float(min(45.0, self.windup_lead_s))
            logger.info("[WINDUP] final_windup=%.1fs (explicit)", result)
            return result
        _hermes_enabled = ENV.get_bool("HLEDAC_ENABLE_HERMES_SYNTHESIS")
        if self.aggressive_mode:
            ratio = 0.15
        elif self.sprint_duration_s <= 120.0:
            ratio = 0.2
        elif self.sprint_duration_s <= 300.0:
            ratio = 0.25
        else:
            ratio = 0.3
        raw = self.sprint_duration_s * ratio
        result = float(max(30.0, min(180.0, raw)))
        logger.info("[WINDUP] lead=%.1fs hermes=%s", result, _hermes_enabled)
        return result

    def windup_for_cycle(self, cycle_time_ema: float) -> float:
        """
        F273B + F278A: Cycle-time-adaptive windup lead.

        The base `effective_windup_lead_s` (30% of duration, clamped [30, 180])
        is the static floor. This method returns a longer windup when observed
        cycles are slow -- so the windup phase has at least 2 cycles of headroom
        for pattern extraction, synthesis, and DuckDB ingest.

        Formula (F290):
          base = effective_windup_lead_s  (adaptive 20/25/30% ratio)
          adapt = max(0, (cycle_time_ema - 8) * 0.5)  # +0.5s per s over 8s cycle
          adapt = min(30.0, adapt)         # cap the bonus at 30s
          return clamp(base + adapt, 30, 180)

        Examples (300s sprint, base=75s, F290 25%):
          - cycle_time_ema=5s  -> 75s (no bonus, quick cycles)
          - cycle_time_ema=20s -> 81s (+6s bonus)
          - cycle_time_ema=60s -> 105s (+30s bonus)

        Examples (100s sprint, base=20s, F290 20%):
          - cycle_time_ema=5s  -> 30s (floor active since base+bonus < 30)
          - cycle_time_ema=30s -> 41s (+11s bonus)
          - cycle_time_ema=60s -> 60s (bonus saturates below ceiling)

        Always-on, bounded [30, 180], fail-soft (negative cycle_time_ema -> base).
        """
        if cycle_time_ema <= 0:
            return self.effective_windup_lead_s
        base = self.effective_windup_lead_s
        adapt = max(0.0, min(30.0, (cycle_time_ema - 8.0) * 0.5))
        return float(max(30.0, min(180.0, base + adapt)))

    @property
    def effective_cycle_sleep_s(self) -> float:
        """
        F228G: Adaptive cycle sleep that scales with sprint duration.

        Short sprints (60-90s) need a much shorter inter-cycle sleep than
        long ones (1800s). For very short sprints the 5.0s default sleep
        consumes up to 50% of the active window -- making it impossible to
        run more than a handful of cycles before windup.

        Returns:
          - 60s quick (active=30s) -> 1.0s (fits ~25 cycles)
          - 300s deep  (active=210s) -> 2.0s (fits ~50 cycles)
          - 600s thoro (active=420s) -> 3.0s
          - 1800s default (active=1620s) -> 5.0s (preserves pre-F228G behavior)

        Bounded: clamp [0.5, 5.0]s to prevent both over-sleep on quick
        sprints and ultra-tight loops on long ones.

        Fail-safe: if active <= 0, returns 0.5s (minimum).
        """
        active = max(0.0, self.sprint_duration_s - self.final_windup_lead_s)
        if active <= 0:
            return 0.5
        scaled = max(0.5, min(5.0, active / 300.0))
        return float(scaled)

    @property
    def hermes_budget_s(self) -> int:
        """
        F253: Adaptive Hermes synthesis budget = 35% of the active window,
        floored at 30s. Prevents short sprints from starving the synthesis
        lane while ensuring long sprints reserve enough budget.

        Uses final_windup_lead_s (which reflects MLX vs non-MLX adaptive logic).

        Examples:
          - 60s quick (active=30s) -> 30 (floor)
          - 300s deep non-MLX (active=270s) -> 94 (35%)
          - 300s deep MLX     (active=210s) -> 73 (35%)
          - 600s thoro  (active=420s) -> 147 (35%)
        """
        active = max(0, self.sprint_duration_s - self.final_windup_lead_s)
        return max(30, int(active * 0.35))

    acquisition_profile: str | None = None
    require_nonfeed_corrob_for_early_exit: bool = False
    sensitive_query_transport: str = "auto"
    predecessor_sprint_id: str | None = None
    deep_research_enabled: bool = False
    extreme_mode: bool = False

    def tier_of(self, source: str) -> SourceTier:
        return self.source_tier_map.get(source, SourceTier.OTHER)

    def sorted_tiers(self) -> list[SourceTier]:
        return _TIER_ORDER.copy()


class EarlyExitClass:
    """

    Sprint F215D: Canonical early exit classification for sprint runs.



    Enforces that active300/600 runs that complete in < 90% of planned

    duration are NOT reported ambiguously as completed -- they must have an

    explicit early exit class.



    Values:

        completed_full_duration              -- ran to or past planned duration

        early_complete_no_work_remaining    -- work loop exited because no feed work remained

        early_complete_return_guard_satisfied -- return_guard passed, windup entered legitimately early

        early_complete_feed_only            -- feed-only run with zero nonfeed accepted findings

        feed_dominant_nonfeed_rescue_attempted -- feed dominant, nonfeed rescue window was attempted (F220D)

        aborted_by_memory                   -- aborted due to memory pressure / governor emergency

        aborted_by_deadline                 -- hard deadline exceeded before completion

        aborted_by_error                    -- exception in run() loop caused abort

    """

    COMPLETED_FULL_DURATION = "completed_full_duration"
    EARLY_COMPLETE_NO_WORK_REMAINING = "early_complete_no_work_remaining"
    EARLY_COMPLETE_RETURN_GUARD_SATISFIED = "early_complete_return_guard_satisfied"
    EARLY_COMPLETE_FEED_ONLY = "early_complete_feed_only"
    EARLY_COMPLETE_PRELUDE_COMPLETE = "early_complete_prelude_complete"
    FEED_DOMINANT_NONFEED_RESCUE_ATTEMPTED = "feed_dominant_nonfeed_rescue_attempted"
    ABORTED_BY_MEMORY = "aborted_by_memory"
    ABORTED_BY_DEADLINE = "aborted_by_deadline"
    ABORTED_BY_ERROR = "aborted_by_error"


class FeedDominanceGuardResult(msgspec.Struct, frozen=True, gc=False):
    """F214: Result of FeedDominanceGuard.compute()."""

    feed_dominance_ratio: float
    nonfeed_accepted_findings: int
    feed_dominance_class: str
    should_recommend_nonfeed_diagnostic: bool
    guard_triggered: bool
    block_early_exit: bool
    reason: str


class LaneBudgetAllocation(msgspec.Struct, gc=False):
    lane_name: str
    allocated_s: float = 0.0
    consumed_s: float = 0.0
    released_s: float = 0.0
    timeout_count: int = 0


class LaneBudgetPool(msgspec.Struct, gc=False):
    _allocations: dict = msgspec.field(default_factory=dict)
    _total_budget_s: float = 0.0

    def allocate(self, lane_name: str, budget_s: float) -> None:
        if lane_name not in self._allocations:
            self._allocations[lane_name] = LaneBudgetAllocation(lane_name=lane_name)
        self._allocations[lane_name].allocated_s += budget_s
        self._total_budget_s += budget_s

    def consume(self, lane_name: str, elapsed_s: float) -> None:
        if lane_name in self._allocations:
            self._allocations[lane_name].consumed_s += elapsed_s

    def release(self, lane_name: str, remaining_s: float | None = None) -> float:
        if lane_name not in self._allocations:
            return 0.0
        alloc = self._allocations[lane_name]
        alloc.timeout_count += 1
        if remaining_s is not None and remaining_s > 0:
            alloc.released_s += remaining_s
            return remaining_s
        return 0.0

    def get_utilization(self) -> float:
        if self._total_budget_s <= 0:
            return -1.0
        total = sum((a.consumed_s for a in self._allocations.values()))
        return min(total / self._total_budget_s, 1.0)

    def get_lane_stats(self) -> dict:
        return {
            n: {
                "allocated_s": a.allocated_s,
                "consumed_s": a.consumed_s,
                "released_s": a.released_s,
                "timeout_count": a.timeout_count,
            }
            for n, a in self._allocations.items()
        }


class FeedDominanceGuard(msgspec.Struct, gc=False):
    """

    F214: Canonical feed dominance guard policy.



    Computed at early exit classification time. Does NOT change scheduler

    behavior in default (strict=False) mode -- only adds reporting fields

    to SprintSchedulerResult and enriches early_exit_reason.



    With strict=True (default False):

      - Blocks feed-only early exit if nonfeed candidates exist but are unresolved

      - Allows early exit if nonfeed accepted >= min_nonfeed_findings

      - Allows early exit if all eligible nonfeed lanes reached terminal state

      - Allows early exit if nonfeed diagnostic timed out

    """

    dominance_ratio_threshold: float = 0.95
    min_nonfeed_findings: int = 5
    strict: bool = False

    def compute(
        self,
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
        eligible_nonfeed_lanes_terminal: bool = False,
        nonfeed_diagnostic_timed_out: bool = False,
    ) -> FeedDominanceGuardResult:
        if total_accepted == 0:
            return FeedDominanceGuardResult(
                feed_dominance_ratio=0.0,
                nonfeed_accepted_findings=0,
                feed_dominance_class="balanced",
                should_recommend_nonfeed_diagnostic=False,
                guard_triggered=False,
                block_early_exit=False,
                reason="no findings",
            )
        ratio = feed_accepted / total_accepted
        nonfeed = nonfeed_accepted
        match ratio:
            case r if r >= 0.999:
                dom_class = "feed_only_like"
            case r if r > self.dominance_ratio_threshold:
                dom_class = "feed_dominant"
            case _:
                dom_class = "balanced"
        should_recommend = ratio > self.dominance_ratio_threshold and nonfeed < 5
        guard_triggered = ratio > self.dominance_ratio_threshold
        block_early_exit = False
        match (self.strict, guard_triggered, nonfeed, eligible_nonfeed_lanes_terminal, nonfeed_diagnostic_timed_out):
            case [False, _, _, _, _]:
                block_early_exit = False
            case [True, False, _, _, _]:
                block_early_exit = False
            case [True, True, n, _, _] if n >= self.min_nonfeed_findings:
                block_early_exit = False
            case [True, True, _, True, _]:
                block_early_exit = False
            case [True, True, _, _, True]:
                block_early_exit = False
            case [True, True, _, _, _]:
                block_early_exit = True
        return FeedDominanceGuardResult(
            feed_dominance_ratio=ratio,
            nonfeed_accepted_findings=nonfeed,
            feed_dominance_class=dom_class,
            should_recommend_nonfeed_diagnostic=should_recommend,
            guard_triggered=guard_triggered,
            block_early_exit=block_early_exit,
            reason=f"feed_dominance={dom_class}:{ratio:.3f}:feed={feed_accepted}:nonfeed={nonfeed}",
        )


class HealthReport(msgspec.Struct, frozen=True, gc=False):
    """

    F228F: Pre-run health check result for critical dependencies.
    F265.1: Extended with EvidenceLog, memory pressure checks.

    Returned by SprintScheduler.health_check() -- NEVER raises.

    """

    duckdb_ok: bool = False
    hermes_ok: bool = False
    fetch_coordinator_ok: bool = False
    graph_service_ok: bool = False
    nym_circuit_open: bool = False
    evidence_log_ok: bool = False
    memory_pressure_ok: bool = False
    overall_ok: bool = False
    blocking_ok: bool = False
    errors: list[str] = msgspec.field(default_factory=list)

    def summary(self) -> str:
        return f"duckdb={('OK' if self.duckdb_ok else 'FAIL')} hermes={('OK' if self.hermes_ok else 'FAIL')} fetch={('OK' if self.fetch_coordinator_ok else 'not_initialized')} graph={('OK' if self.graph_service_ok else 'not_initialized')} elog={('OK' if self.evidence_log_ok else 'FAIL')} mem={('OK' if self.memory_pressure_ok else 'CRITICAL')} overall={('OK' if self.overall_ok else 'DEGRADED')}"


@dataclass(slots=True)
class SprintSchedulerResult:
    """

    Outcome of one sprint run.



    Attributes:

        cycles_started: Number of fetch cycles initiated.

        cycles_completed: Number of fetch cycles that completed all phases.

        unique_entry_hashes_seen: Count of deduplicated entries processed.

        duplicate_entry_hashes_skipped: Count of duplicate entries filtered.

        total_pattern_hits: Sum of pattern matches across all sources.

        accepted_findings: Findings that passed quality gate.

        entries_per_source: Breakdown of entries by source (source_name -> count).

        hits_per_source: Pattern hits per source (source_name -> count).

        final_phase: Last phase reached (BOOT, GATHER, JUDGMENT, EXPORT, TEARDOWN).

        export_paths: List of paths where sprint results were exported.

        aborted: True if sprint was aborted early.

        abort_reason: Human-readable reason for abortion.

        stop_requested: True when stop_on_first_accepted triggered acceptance.

        public_discovered: Public pipeline discoveries (F8XE).

        public_fetched: Public pipeline successful fetches.

        public_matched_patterns: Public pipeline pattern matches.

        public_accepted_findings: Public pipeline accepted findings.

        public_stored_findings: Public pipeline stored findings.

        public_error: Public pipeline error message.

        ct_log_discovered: CT log discoveries (F193A).

        ct_log_stored: CT log stored findings.

        ct_log_accepted_findings: CT log accepted findings (F194A).

        ct_log_error: CT log error message.

        entered_active_at_monotonic: Timestamp when ACTIVE phase first entered.

        pre_loop_elapsed_s: Wall-clock seconds from run() to loop guard entry.

        first_cycle_started_at_monotonic: Timestamp of first cycles_started increment.

        pre_active_starved: True when gap between entered_active and first_cycle_started > 30s.

    """

    cycles_started: int = 0
    cycles_completed: int = 0
    consecutive_empty_cycles: int = 0
    max_consecutive_empty_cycles: int = 0
    unique_entry_hashes_seen: int = 0
    duplicate_entry_hashes_skipped: int = 0
    total_pattern_hits: int = 0
    entries_seen: int = 0
    entries_scanned: int = 0
    entries_with_hits: int = 0
    findings_built_pre_store: int = 0
    signal_stage: str = "unknown"
    accepted_findings: int = 0
    entries_per_source: dict[str, int] = field(default_factory=dict)
    hits_per_source: dict[str, int] = field(default_factory=dict)
    final_phase: str = "BOOT"
    export_paths: list[str] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""
    stop_requested: bool = False
    synthesis_success: bool = False
    synthesis_engine: str = "unknown"
    synthesis_findings_count: int = 0
    ioc_cooccurrence_edges: int = 0
    synthesis_text: str = ""
    hypotheses_generated: int = 0
    pii_findings_anonymized: int = 0
    public_discovered: int = 0
    public_fetched: int = 0
    public_matched_patterns: int = 0
    public_accepted_findings: int = 0
    public_stored_findings: int = 0
    public_error: str = ""
    public_provider_selection_debug: dict = field(default_factory=dict)
    ct_log_discovered: int = 0
    ct_log_stored: int = 0
    ct_log_accepted_findings: int = 0
    ct_log_error: str = ""
    ct_loss_stage: str = "no_loss"
    ct_bridge_invoked: bool = False
    ct_raw_sample_keys: tuple[str, ...] = ()
    ct_raw_sample_count: int = 0
    ct_raw_count: int = 0
    ct_candidates_built: int = 0
    ct_bridge_rejections_count: int = 0
    ct_bridge_rejection_reasons: tuple[str, ...] = ()
    ct_candidates_accumulated: int = 0
    ct_candidates_stored: int = 0
    ct_storage_rejected: int = 0
    ct_storage_rejection_reasons: tuple[str, ...] = ()
    ct_candidate_count: int = 0
    ct_valid_domain_count: int = 0
    ct_bridge_build_success_count: int = 0
    ct_bridge_quality_rejected_count: int = 0
    ct_raw_domains_seen: int = 0
    ct_unique_domains_seen: int = 0
    ct_valid_public_domains: int = 0
    ct_wildcard_domains: int = 0
    ct_private_reserved_domains: int = 0
    ct_duplicate_candidates: int = 0
    ct_expansion_clues_count: int = 0
    ct_candidate_examples: tuple[str, ...] = ()
    quality_rejection_ledger: tuple = ()
    quality_rejection_summary_by_family: dict = field(default_factory=dict)
    duplicate_rejection_summary_by_family: dict = field(default_factory=dict)
    low_information_by_family: dict = field(default_factory=dict)
    ct_quarantine_count: int = 0
    ct_quarantine_samples: tuple[str, ...] = ()
    ct_provider_status: str = ""
    ct_cache_used: bool = False
    ct_cache_stale: bool = False
    ct_cache_age_s: float = 0.0
    ct_planned: bool = False
    ct_scheduled: bool = False
    ct_provider_selected: str = ""
    ct_request_attempted: bool = False
    ct_request_timeout: bool = False
    ct_raw_count: int = 0
    ct_bridge_invoked: bool = False
    ct_candidates_built: int = 0
    ct_storage_attempted: bool = False
    ct_storage_accepted: bool = False
    ct_terminal_stage: str = ""
    ct_prelude_missing_but_final_attempted: bool = False
    entered_active_at_monotonic: float | None = None
    pre_loop_elapsed_s: float | None = None
    first_cycle_started_at_monotonic: float | None = None
    pre_active_starved: bool = False
    pre_loop_blocker_reason: str = ""
    dedup_preload_count: int | None = None
    dedup_preload_elapsed_s: float | None = None
    feed_zero_yield_detected: bool = False
    feed_inaccessible_detected: bool = False
    feed_content_empty_detected: bool = False
    feed_no_pattern_with_content: bool = False
    findings_build_loss_detected: bool = False
    feed_no_signal_sources: list[str] = field(default_factory=list)
    policy_quality_feedback_calls: int = 0
    policy_quality_feedback_decisions: int = 0
    policy_quality_feedback_sources: int = 0
    policy_quality_feedback_errors: int = 0
    public_backend_degraded: bool = False
    dominant_public_blocker: str = ""
    dominant_feed_blocker: str = ""
    dominant_branch_blocker: str = ""
    branch_degradation_summary: str = ""
    branch_timeout_count: int = 0
    branch_skipped_remaining_too_low: int = 0
    public_branch_timed_out: bool = False
    ct_branch_timed_out: bool = False
    findings_deduplicated: int = 0
    hypothesis_contradictions_detected: int = 0
    cover_traffic_fired: int = 0
    hermes_model_loaded: bool = False
    hermes_load_attempted: bool = False
    hermes_load_reason: str = ""
    hermes_load_elapsed_s: float = 0.0
    mlx_batcher_stats: dict = field(default_factory=dict)
    pattern_extraction_drain_completed: int = 0
    pattern_extraction_drain_timed_out: int = 0
    pattern_extraction_drain_elapsed_s: float = 0.0
    malloc_pressure_relief_count: int = 0
    malloc_pressure_relief_last_rc: int = 0
    malloc_pressure_relief_last_at_s: float = 0.0
    dynamic_branch_floor_s: float = 0.0
    effective_windup_lead_used_s: float = 0.0
    windup_lead_adaptive_factor: float = 1.0
    captcha_hits: int = 0
    circuit_breaker_opens: int = 0
    rl_suggested_pivot: str = ""
    duckdb_mode: str = "unknown"
    forensics_enriched_ct_findings: int = 0
    multimodal_enriched_findings: int = 0
    identity_candidates_found: int = 0
    identity_findings_produced: int = 0
    exposure_findings_produced: int = 0
    correlated_assets_count: int = 0
    leak_findings_produced: int = 0
    timeline_findings_produced: int = 0
    evidence_triage_findings_count: int = 0
    sprint_diff_findings_produced: int = 0
    kill_chain_tags_produced: int = 0
    wayback_diff_findings_produced: int = 0
    chain_steps_recorded: int = 0
    rir_correlation_produced: int = 0
    sidecars_skipped: tuple[str, ...] = ()
    acquisition_lanes_skipped: int = 0
    peak_rss_gib: float = 0.0
    budget_violations: int = 0
    governor_uma_state: str = ""
    governor_system_used_gib: float = 0.0
    governor_swap_detected: bool = False
    governor_io_only: bool = False
    pressure_violations: int = 0
    cc_archive_injected: int = 0
    academic_findings_count: int = 0
    dht_findings_produced: int = 0
    rdap_enrichment_attempted: int = 0
    rdap_enrichment_findings_built: int = 0
    rdap_enrichment_findings_stored: int = 0
    rdap_enrichment_rejections: int = 0
    rdap_enrichment_error: str | None = None
    security_rejected_count: int = 0
    pii_redacted_count: int = 0
    rl_enabled: bool = False
    rl_epsilon: float = 0.0
    rl_total_reward: float = 0.0
    rl_last_action: int = 0
    rl_lane_combo: frozenset = field(default_factory=frozenset)
    acquisition_lane_outcomes: tuple = ()
    lane_ct_accepted_findings: int = 0
    lane_wayback_accepted_findings: int = 0
    lane_pdns_accepted_findings: int = 0
    lane_blockchain_accepted_findings: int = 0
    lane_ipfs_accepted_findings: int = 0
    lane_public_accepted_findings: int = 0
    ipfs_cids_attempted: int = 0
    ipfs_findings_accepted: int = 0
    lane_doh_accepted_findings: int = 0
    doh_planned: bool = False
    doh_scheduled: bool = False
    doh_request_attempted: bool = False
    doh_domains_attempted: int = 0
    doh_raw_count: int = 0
    doh_accepted_findings: int = 0
    doh_terminal_stage: str = ""
    doh_provider_errors: tuple[str, ...] = ()
    doh_cache_used: bool = False
    doh_seed_source: str = ""
    wayback_attempted: bool = False
    wayback_raw_count: int = 0
    wayback_candidates_built: int = 0
    wayback_accepted_count: int = 0
    graph_rag_context_count: int = 0
    passive_dns_attempted: bool = False
    passive_dns_raw_count: int = 0
    passive_dns_candidates_built: int = 0
    passive_dns_accepted_count: int = 0
    wayback_advisory_clues_count: int = 0
    wayback_changed_url_count: int = 0
    wayback_added_url_count: int = 0
    wayback_digest_changed_count: int = 0
    wayback_unchanged_rejected: int = 0
    passive_dns_advisory_clues_count: int = 0
    passive_dns_private_ip_rejected: int = 0
    passive_dns_empty_ip_rejected: int = 0
    nonfeed_predispatch_attempted: bool = False
    nonfeed_predispatch_skipped: dict[str, str] = field(default_factory=dict)
    nonfeed_predispatch_lanes: tuple[str, ...] = ()
    nonfeed_predispatch_duration_s: float = 0.0
    windup_blocked_until_nonfeed_attempted: bool = False
    nonfeed_plan_debug: NonfeedPlanDebug | None = None
    prewindup_barrier_checked: bool = False
    prewindup_barrier_required_lanes: tuple[str, ...] = ()
    prewindup_barrier_satisfied: bool = False
    prewindup_barrier_attempted_lanes: tuple[str, ...] = ()
    prewindup_barrier_skipped_lanes: dict[str, str] = field(default_factory=dict)
    prewindup_barrier_errors: dict[str, str] = field(default_factory=dict)
    prewindup_barrier_duration_s: float = 0.0
    windup_delayed_for_nonfeed: bool = False
    prewindup_barrier_delayed_cycle: bool = False
    windup_guard_call_count: int = 0
    windup_guard_callback_supplied_count: int = 0
    windup_guard_callback_executed_count: int = 0
    windup_guard_last_reason: str = ""
    windup_guard_last_phase: str = ""
    windup_guard_last_allowed: bool | None = None
    windup_guard_last_callback_not_executed_reason: str = ""
    prewindup_guard_async_bridge_used: bool = False
    prewindup_guard_async_error: str = ""
    prewindup_guard_fail_closed: bool = False
    return_guard_checked: bool = False
    return_guard_required_lanes: tuple[str, ...] = ()
    return_guard_satisfied: bool = False
    return_guard_delayed_for_nonfeed: bool = False
    return_guard_block_reason: str = ""
    return_guard_attempted_lanes: tuple[str, ...] = ()
    return_guard_skipped_lanes: dict[str, str] = field(default_factory=dict)
    return_guard_errors: dict[str, str] = field(default_factory=dict)
    dark_surface_pivots_attempted: int = 0
    dark_surface_pivots_accepted: int = 0
    gopher_findings_ingested: int = 0
    bgp_enrichment_findings_ingested: int = 0
    banner_grab_findings_ingested: int = 0
    scheduler_exit_path: str | None = None
    scheduler_exit_reason: str | None = None
    scheduler_exit_phase: str | None = None
    scheduler_exit_cycle: int | None = None
    scheduler_exit_elapsed_s: float | None = None
    scheduler_exit_guard_checked: bool = False
    scheduler_exit_guard_required: tuple[str, ...] = ()
    scheduler_exit_guard_satisfied: bool | None = None
    hard_deadline_monotonic: float | None = None
    hard_deadline_checked_count: int = 0
    hard_deadline_exceeded: bool = False
    hard_deadline_exceeded_at_cycle: int | None = None
    hard_deadline_remaining_s_at_exit: float | None = None
    acquisition_terminality_checked: bool = False
    acquisition_terminality_satisfied: bool = False
    acquisition_terminality_missing_lanes: tuple[str, ...] = ()
    acquisition_terminality_report: dict = field(default_factory=dict)
    nonfeed_predispatch_checked: bool = False
    nonfeed_predispatch_ran: bool = False
    nonfeed_predispatch_reason: str | None = None
    nonfeed_predispatch_outcomes_count: int = 0
    acquisition_prelude_checked: bool = False
    acquisition_prelude_ran: bool = False
    acquisition_prelude_required_lanes: tuple[str, ...] = ()
    acquisition_prelude_terminal_lanes: tuple[str, ...] = ()
    acquisition_prelude_missing_lanes: tuple[str, ...] = ()
    acquisition_prelude_skipped_lanes: dict[str, str] = field(default_factory=dict)
    acquisition_prelude_errors: dict[str, str] = field(default_factory=dict)
    acquisition_prelude_duration_s: float = 0.0
    acquisition_prelude_reason: str = ""
    acquisition_prelude_domain_detected: bool = False
    acquisition_prelude_plan_present: bool = False
    acquisition_prelude_plan_built_for_prelude: bool = False
    acquisition_prelude_domain_detection_error: str = ""
    acquisition_plan_build_failed: bool = False
    acquisition_plan_build_error_type: str = ""
    acquisition_plan_build_error: str = ""
    feed_budget_active: bool = False
    feed_budget_reason: str = ""
    feed_accepted_before_cap: int = 0
    feed_suppressed_by_budget: int = 0
    feed_budget_per_source: dict[str, int] = field(default_factory=dict)
    top_feed_source_counts: tuple[tuple[str, int], ...] = ()
    max_per_source_applied: str = ""
    nonfeed_budget_active: bool = False
    nonfeed_budget_expected_lanes: tuple[str, ...] = ()
    nonfeed_budget_terminal_lanes: tuple[str, ...] = ()
    nonfeed_budget_unresolved_lanes: tuple[str, ...] = ()
    feed_suppressed_by_nonfeed_budget: int = 0
    feed_suppression_count: int = 0
    feed_suppression_reason: str = ""
    nonfeed_prelude_enabled: bool = False
    nonfeed_prelude_expected_lanes: tuple[str, ...] = ()
    nonfeed_prelude_attempted_lanes: tuple[str, ...] = ()
    nonfeed_prelude_terminal_lanes: tuple[str, ...] = ()
    nonfeed_prelude_missing_lanes: tuple[str, ...] = ()
    nonfeed_prelude_accepted_by_lane: dict[str, int] = field(default_factory=dict)
    nonfeed_prelude_error_by_lane: dict[str, str] = field(default_factory=dict)
    nonfeed_prelude_duration_s: float = 0.0
    nonfeed_prelude_feed_blocked_until_complete: bool = False
    nonfeed_priority_enabled: bool = False
    nonfeed_profile_expected_lanes: tuple[str, ...] = ()
    nonfeed_expected_lanes: tuple[str, ...] = ()
    nonfeed_expected_lanes_source: str = ""
    feed_domain_seeds: tuple[str, ...] = ()
    arrow_batch_hard_cap: int = 0
    arrow_batch_dropped_after_flush_failure: int = 0
    # ISSUE-3: BoundedLRUDict eviction counters for memory-bounded SprintRunContext fields.
    seen_hashes_dropped: int = 0
    entries_per_source_dropped: int = 0
    hits_per_source_dropped: int = 0
    novelty_bonuses_dropped: int = 0
    source_weights_dropped: int = 0
    feed_accepted_per_source_dropped: int = 0
    fetch_latency_ema_dropped: int = 0
    arrow_last_flush_error: str = ""
    arrow_metrics: dict = field(default_factory=dict)
    transport_efficiency: dict[str, int] = field(default_factory=dict)
    pivot_lane_plan_count: int = 0
    planned_pivot_lanes: tuple[str, ...] = ()
    seed_quality_checked: bool = False
    seed_quality_keep_count: int = 0
    seed_quality_drop_count: int = 0
    seed_quality_drop_reasons: dict = field(default_factory=dict)
    seed_quality_kept_sample: list = field(default_factory=list)
    seed_quality_dropped_sample: list = field(default_factory=list)
    seed_quality_bypass_reason: str = ""
    requested_duration_s: float = 0.0
    actual_duration_s: float = 0.0
    elapsed_pct: float = 0.0
    active_window_budget_s: float = 0.0
    active_window_elapsed_s: float = 0.0
    windup_efficiency: float = 0.0
    early_exit_class: str = ""
    early_exit_reason: str = ""
    source_family_events: list[dict] = field(default_factory=list)
    MAX_SOURCE_FAMILY_EVENTS: int = 200
    feed_dominance_ratio: float = 0.0
    feed_dominance_class: str = ""
    feed_dominance_guard_triggered: bool = False
    should_recommend_nonfeed_diagnostic: bool = False
    public_terminal_stage: str = ""
    public_stage_counters: dict = field(default_factory=dict)
    public_discovery_empty_reason: str = ""
    nonfeed_mission_active: bool = False
    nonfeed_required_families: tuple[str, ...] = ()
    nonfeed_optional_families: tuple[str, ...] = ()
    nonfeed_family_status: dict[str, str] = field(default_factory=dict)
    nonfeed_all_required_terminal: bool = False
    nonfeed_any_accepted: bool = False
    nonfeed_provider_failures: tuple[str, ...] = ()
    nonfeed_memory_skips: tuple[str, ...] = ()
    nonfeed_mission_exit_reason: str = ""
    nonfeed_candidate_ledger_summary: dict = field(default_factory=dict)
    nonfeed_lane_eligibility: dict[str, bool] = field(default_factory=dict)
    nonfeed_doh_planner_input: list[str] = field(default_factory=list)
    nonfeed_ct_planner_candidates: list[str] = field(default_factory=list)
    nonfeed_wayback_candidates: list[str] = field(default_factory=list)
    nonfeed_passive_dns_candidates: list[str] = field(default_factory=list)
    research_context: ResearchContext | None = None
    acquisition_plan_present_for_prelude: bool = False
    acquisition_plan_lanes_for_prelude: tuple[str, ...] = ()
    acquisition_plan_enabled_lanes_for_prelude: tuple[str, ...] = ()
    acquisition_plan_profile_for_prelude: str = ""
    acquisition_plan_build_error_for_prelude: str = ""
    timer_events: list[dict] | None = None
    _int_counter_layout: IntCounterLayoutProto | None = None
    seed_context_available: bool = False
    seed_context_propagated: bool = False
    lanes_unlocked_by_seed_context: list[str] = field(default_factory=list)
    seed_context_skip_reason: str = ""
    seed_context_source: str = ""
    pivot_seed_count: int = 0
    pivot_seed_type_counts: dict[str, int] = field(default_factory=dict)
    pivot_seed_sample: tuple[str, ...] = ()
    pivot_seed_domains: tuple[str, ...] = ()
    pivot_seed_ips: tuple[str, ...] = ()
    pivot_seed_urls: tuple[str, ...] = ()
    pivot_seed_hashes: tuple[str, ...] = ()
    pivot_seed_cves: tuple[str, ...] = ()
    next_seeds_query_suggestions: tuple[str, ...] = ()
    next_seeds_skip_reason: str = ""
    planner_action_skip_reason: str = ""
    next_seeds_ioc_domains: tuple[str, ...] = ()
    next_seeds_ioc_ips: tuple[str, ...] = ()
    next_seeds_ioc_urls: tuple[str, ...] = ()
    next_seeds_ioc_hashes: tuple[str, ...] = ()
    next_seeds_ioc_cves: tuple[str, ...] = ()
    next_seeds_provider_yield: bool = False
    next_seeds_pivot_deepening: bool = False
    next_seeds_consumed_count: int = 0
    next_seeds_seed_source: str = ""
    planner_actions_consumed_count: int = 0
    planner_action_lanes_requested: list[str] = field(default_factory=list)
    planner_action_seed_source: str = ""
    quantum_path_seeds: list[str] = field(default_factory=list)
    run_error_class: str = ""
    run_error: str = ""

    def __post_init__(self) -> None:
        """
        Sprint P0-1: lazily allocate the SoA counter layout.

        Invariants:
            L.1  Layout is allocated exactly once per instance.
            L.2  Allocation failure (IntCounterLayout unavailable or
                 MemoryError) is fail-soft: layout remains None and
                 property getters/setters return 0 (counter-only).
            L.3  Idempotent — safe to call multiple times.
        """
        if self._int_counter_layout is not None:
            return
        _layout_class = IntCounterLayoutRust if IntCounterLayoutRust is not None else IntCounterLayout
        if _layout_class is None:
            return
        try:
            object.__setattr__(self, "_int_counter_layout", _layout_class(INT_COUNTER_LAYOUT_NAMES))
        except Exception:  # noqa: BLE001 — best-effort; int_counter fallback; returns 0
            object.__setattr__(self, "_int_counter_layout", None)

    @property
    def cycles_started(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("cycles_started")

    @cycles_started.setter
    def cycles_started(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("cycles_started", value)

    @property
    def cycles_completed(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("cycles_completed")

    @cycles_completed.setter
    def cycles_completed(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("cycles_completed", value)

    @property
    def consecutive_empty_cycles(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("consecutive_empty_cycles")

    @consecutive_empty_cycles.setter
    def consecutive_empty_cycles(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("consecutive_empty_cycles", value)

    @property
    def unique_entry_hashes_seen(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("unique_entry_hashes_seen")

    @unique_entry_hashes_seen.setter
    def unique_entry_hashes_seen(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("unique_entry_hashes_seen", value)

    @property
    def duplicate_entry_hashes_skipped(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("duplicate_entry_hashes_skipped")

    @duplicate_entry_hashes_skipped.setter
    def duplicate_entry_hashes_skipped(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("duplicate_entry_hashes_skipped", value)

    @property
    def hard_deadline_checked_count(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("hard_deadline_checked_count")

    @hard_deadline_checked_count.setter
    def hard_deadline_checked_count(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("hard_deadline_checked_count", value)

    @property
    def windup_guard_call_count(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("windup_guard_call_count")

    @windup_guard_call_count.setter
    def windup_guard_call_count(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("windup_guard_call_count", value)

    @property
    def windup_guard_callback_supplied_count(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("windup_guard_callback_supplied_count")

    @windup_guard_callback_supplied_count.setter
    def windup_guard_callback_supplied_count(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("windup_guard_callback_supplied_count", value)

    @property
    def windup_guard_callback_executed_count(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("windup_guard_callback_executed_count")

    @windup_guard_callback_executed_count.setter
    def windup_guard_callback_executed_count(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("windup_guard_callback_executed_count", value)

    @property
    def policy_quality_feedback_calls(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("policy_quality_feedback_calls")

    @policy_quality_feedback_calls.setter
    def policy_quality_feedback_calls(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("policy_quality_feedback_calls", value)

    @property
    def policy_quality_feedback_errors(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("policy_quality_feedback_errors")

    @policy_quality_feedback_errors.setter
    def policy_quality_feedback_errors(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("policy_quality_feedback_errors", value)

    @property
    def ipfs_cids_attempted(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("ipfs_cids_attempted")

    @ipfs_cids_attempted.setter
    def ipfs_cids_attempted(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("ipfs_cids_attempted", value)

    @property
    def multimodal_enriched_findings(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("multimodal_enriched_findings")

    @multimodal_enriched_findings.setter
    def multimodal_enriched_findings(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("multimodal_enriched_findings", value)

    @property
    def feed_suppression_count(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("feed_suppression_count")

    @feed_suppression_count.setter
    def feed_suppression_count(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("feed_suppression_count", value)

    @property
    def forensics_enriched_ct_findings(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("forensics_enriched_ct_findings")

    @forensics_enriched_ct_findings.setter
    def forensics_enriched_ct_findings(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("forensics_enriched_ct_findings", value)

    @property
    def acquisition_lanes_skipped(self) -> int:
        if self._int_counter_layout is None:
            return 0
        return self._int_counter_layout.get("acquisition_lanes_skipped")

    @acquisition_lanes_skipped.setter
    def acquisition_lanes_skipped(self, value: int) -> None:
        if self._int_counter_layout is not None:
            self._int_counter_layout.set("acquisition_lanes_skipped", value)

    def bump_counter(self, name: str, n: int = 1) -> int:
        """
        Sprint P0-1: increment a hot-path counter by `n` (default 1) on
        the SoA layout. Returns the new value, or 0 on layout miss.

        Usage:
            result.bump_counter("cycles_started")         # +1
            result.bump_counter("cycles_completed", n=2)  # +2

        This is a slightly faster path than `result.cycles_started += 1`
        (skips the property setter) and is the recommended migration
        target for hot-path counter bumps in a follow-up sprint.

        Fail-soft: layout unavailable → returns 0.
        """
        if self._int_counter_layout is None:
            return 0
        try:
            return self._int_counter_layout.bump(name, n)
        except Exception:  # noqa: BLE001 — best-effort; int_counter fallback; returns 0
            return 0

    pivot_graph_stats_used: bool = False
    pivot_graph_stats_keys: tuple[str, ...] = ()
    graph_aware_pivot_count: int = 0
    pivot_integration_reason: str = ""
    _int_counter_layout = None


INT_COUNTER_LAYOUT_NAMES: tuple[str, ...] = (
    "cycles_started",
    "cycles_completed",
    "consecutive_empty_cycles",
    "unique_entry_hashes_seen",
    "duplicate_entry_hashes_skipped",
    "hard_deadline_checked_count",
    "windup_guard_call_count",
    "windup_guard_callback_supplied_count",
    "windup_guard_callback_executed_count",
    "policy_quality_feedback_calls",
    "policy_quality_feedback_errors",
    "ipfs_cids_attempted",
    "multimodal_enriched_findings",
    "feed_suppression_count",
    "forensics_enriched_ct_findings",
    "acquisition_lanes_skipped",
)


class SprintResultBuilder:
    """
    Fluent builder for SprintSchedulerResult (Issue #6).

    Uses __dataclass_fields__ reflection — no code generation step needed.
    All 100+ fields are supported automatically.

    Usage:
        result = (SprintResultBuilder()
            .with_cycles_started(5)
            .with_cycles_completed(3)
            .with_aborted(True)
            .with_abort_reason("timeout")
            .build())
    """

    __slots__ = ("_result",)

    def __init__(self, base: SprintSchedulerResult | None = None) -> None:
        object.__setattr__(self, "_result", base or SprintSchedulerResult())

    def build(self) -> SprintSchedulerResult:
        """Return the constructed SprintSchedulerResult."""
        return self._result

    @classmethod
    def _field_names(cls) -> list[str]:
        """Reflect field names from SprintSchedulerResult at runtime."""
        return list(SprintSchedulerResult.__dataclass_fields__.keys())

    def _set(self, name: str, value: object) -> "SprintResultBuilder":
        """Internal setter — bypasses __setattr__ for speed."""
        object.__setattr__(self._result, name, value)
        return self

    def __getattr__(self, name: str) -> object:
        if name.startswith("_") or name == "build":
            return object.__getattribute__(self, name)
        return getattr(self._result, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("_") or name == "build":
            object.__setattr__(self, name, value)
        else:
            self._set(name, value)

    def with_cycles_started(self, v: int) -> "SprintResultBuilder":
        return self._set("cycles_started", v) or self

    def with_cycles_completed(self, v: int) -> "SprintResultBuilder":
        return self._set("cycles_completed", v) or self

    def with_aborted(self, v: bool) -> "SprintResultBuilder":
        return self._set("aborted", v) or self

    def with_abort_reason(self, v: str) -> "SprintResultBuilder":
        return self._set("abort_reason", v) or self

    def with_final_phase(self, v: str) -> "SprintResultBuilder":
        return self._set("final_phase", v) or self

    def with_accepted_findings(self, v: int) -> "SprintResultBuilder":
        return self._set("accepted_findings", v) or self

    def with_total_pattern_hits(self, v: int) -> "SprintResultBuilder":
        return self._set("total_pattern_hits", v) or self

    def with_unique_entry_hashes_seen(self, v: int) -> "SprintResultBuilder":
        return self._set("unique_entry_hashes_seen", v) or self

    def with_duplicate_entry_hashes_skipped(self, v: int) -> "SprintResultBuilder":
        return self._set("duplicate_entry_hashes_skipped", v) or self

    def with_consecutive_empty_cycles(self, v: int) -> "SprintResultBuilder":
        return self._set("consecutive_empty_cycles", v) or self

    def with_max_consecutive_empty_cycles(self, v: int) -> "SprintResultBuilder":
        return self._set("max_consecutive_empty_cycles", v) or self

    def with_entries_per_source(self, v: dict[str, int]) -> "SprintResultBuilder":
        return self._set("entries_per_source", v) or self

    def with_hits_per_source(self, v: dict[str, int]) -> "SprintResultBuilder":
        return self._set("hits_per_source", v) or self

    def with_export_paths(self, v: list[str]) -> "SprintResultBuilder":
        return self._set("export_paths", v) or self

    def with_stop_requested(self, v: bool) -> "SprintResultBuilder":
        return self._set("stop_requested", v) or self

    def with_synthesis_success(self, v: bool) -> "SprintResultBuilder":
        return self._set("synthesis_success", v) or self

    def with_synthesis_engine(self, v: str) -> "SprintResultBuilder":
        return self._set("synthesis_engine", v) or self

    def with_synthesis_findings_count(self, v: int) -> "SprintResultBuilder":
        return self._set("synthesis_findings_count", v) or self

    def with_synthesis_text(self, v: str) -> "SprintResultBuilder":
        return self._set("synthesis_text", v) or self

    def with_hypotheses_generated(self, v: int) -> "SprintResultBuilder":
        return self._set("hypotheses_generated", v) or self

    def with_public_discovered(self, v: int) -> "SprintResultBuilder":
        return self._set("public_discovered", v) or self

    def with_public_fetched(self, v: int) -> "SprintResultBuilder":
        return self._set("public_fetched", v) or self

    def with_public_matched_patterns(self, v: int) -> "SprintResultBuilder":
        return self._set("public_matched_patterns", v) or self

    def with_public_accepted_findings(self, v: int) -> "SprintResultBuilder":
        return self._set("public_accepted_findings", v) or self

    def with_public_stored_findings(self, v: int) -> "SprintResultBuilder":
        return self._set("public_stored_findings", v) or self

    def with_public_error(self, v: str) -> "SprintResultBuilder":
        return self._set("public_error", v) or self

    def with_ct_log_discovered(self, v: int) -> "SprintResultBuilder":
        return self._set("ct_log_discovered", v) or self

    def with_ct_log_stored(self, v: int) -> "SprintResultBuilder":
        return self._set("ct_log_stored", v) or self

    def with_ct_log_accepted_findings(self, v: int) -> "SprintResultBuilder":
        return self._set("ct_log_accepted_findings", v) or self

    def with_ct_log_error(self, v: str) -> "SprintResultBuilder":
        return self._set("ct_log_error", v) or self

    def with_entered_active_at_monotonic(self, v: float) -> "SprintResultBuilder":
        return self._set("entered_active_at_monotonic", v) or self

    def with_pre_loop_elapsed_s(self, v: float) -> "SprintResultBuilder":
        return self._set("pre_loop_elapsed_s", v) or self

    def with_first_cycle_started_at_monotonic(self, v: float) -> "SprintResultBuilder":
        return self._set("first_cycle_started_at_monotonic", v) or self

    def with_pre_active_starved(self, v: bool) -> "SprintResultBuilder":
        return self._set("pre_active_starved", v) or self

    def with_(self, field: str, value: object) -> "SprintResultBuilder":
        """
        Generic setter for any field by name.
        Use for fields without dedicated with_ methods.

        Example:
            builder.with_('quantum_path_seeds', ['seed1', 'seed2'])
        """
        if field not in SprintSchedulerResult.__dataclass_fields__:
            raise AttributeError(f"Unknown field: {field}")
        self._set(field, value)
        return self

    def update(self, **kwargs: object) -> "SprintResultBuilder":
        """
        Batch update multiple fields at once.

        Example:
            builder.update(
                cycles_started=5,
                aborted=True,
                abort_reason="timeout"
            )
        """
        for k, v in kwargs.items():
            self._set(k, v)
        return self


class SprintResult:
    """

    Universal fields -- always populated regardless of sprint mode.



    This is the base class for all result variants. Subclasses add

    mode-specific fields that are guaranteed to be populated when

    that mode's pipeline ran.



    Use factory methods on SprintScheduler to construct variants from

    internal _result state when needed. The types are a foundation for

    gradual migration away from the monolithic SprintSchedulerResult.

    """

    cycles_started: int = 0
    cycles_completed: int = 0
    unique_entry_hashes_seen: int = 0
    duplicate_entry_hashes_skipped: int = 0
    total_pattern_hits: int = 0
    entries_seen: int = 0
    entries_scanned: int = 0
    entries_with_hits: int = 0
    findings_built_pre_store: int = 0
    signal_stage: str = "unknown"
    accepted_findings: int = 0
    entries_per_source: dict[str, int] = field(default_factory=dict)
    hits_per_source: dict[str, int] = field(default_factory=dict)
    final_phase: str = "BOOT"
    export_paths: list[str] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""
    stop_requested: bool = False
    entered_active_at_monotonic: float | None = None
    pre_loop_elapsed_s: float | None = None
    first_cycle_started_at_monotonic: float | None = None
    pre_active_starved: bool = False
    pre_loop_blocker_reason: str = ""
    lane_elapsed_s: dict[str, float] = field(default_factory=dict)
    cycle_elapsed_s: list[float] = field(default_factory=list)
    forensics_enriched_ct_findings: int = 0
    multimodal_enriched_findings: int = 0
    identity_candidates_found: int = 0
    identity_findings_produced: int = 0
    exposure_findings_produced: int = 0
    correlated_assets_count: int = 0
    leak_findings_produced: int = 0
    timeline_findings_produced: int = 0
    evidence_triage_findings_count: int = 0
    sprint_diff_findings_produced: int = 0
    kill_chain_tags_produced: int = 0
    wayback_diff_findings_produced: int = 0
    chain_steps_recorded: int = 0
    rir_correlation_produced: int = 0
    sidecars_skipped: tuple[str, ...] = ()
    acquisition_lanes_skipped: int = 0
    peak_rss_gib: float = 0.0
    budget_violations: int = 0
    transport_efficiency: dict[str, int] = field(default_factory=dict)
    branch_timeout_count: int = 0
    public_branch_timed_out: bool = False
    ct_branch_timed_out: bool = False
    cc_archive_injected: int = 0
    academic_findings_count: int = 0
    scheduler_exit_path: str | None = None
    scheduler_exit_reason: str | None = None
    scheduler_exit_phase: str | None = None
    scheduler_exit_cycle: int | None = None
    scheduler_exit_elapsed_s: float | None = None
    scheduler_exit_guard_checked: bool = False
    scheduler_exit_guard_required: tuple[str, ...] = ()
    scheduler_exit_guard_satisfied: bool | None = None
    hard_deadline_monotonic: float | None = None
    hard_deadline_checked_count: int = 0
    hard_deadline_exceeded: bool = False
    hard_deadline_exceeded_at_cycle: int | None = None
    hard_deadline_remaining_s_at_exit: float | None = None
    acquisition_terminality_checked: bool = False
    acquisition_terminality_satisfied: bool = False
    acquisition_terminality_missing_lanes: tuple[str, ...] = ()
    acquisition_terminality_report: dict = field(default_factory=dict)
    nonfeed_predispatch_checked: bool = False
    nonfeed_predispatch_ran: bool = False
    nonfeed_predispatch_reason: str | None = None
    nonfeed_predispatch_outcomes_count: int = 0
    acquisition_prelude_checked: bool = False
    acquisition_prelude_ran: bool = False
    acquisition_prelude_required_lanes: tuple[str, ...] = ()
    acquisition_prelude_terminal_lanes: tuple[str, ...] = ()
    acquisition_prelude_missing_lanes: tuple[str, ...] = ()
    acquisition_prelude_skipped_lanes: dict[str, str] = field(default_factory=dict)
    acquisition_prelude_errors: dict[str, str] = field(default_factory=dict)
    acquisition_prelude_duration_s: float = 0.0
    acquisition_prelude_reason: str = ""
    acquisition_prelude_domain_detected: bool = False
    acquisition_prelude_plan_present: bool = False
    acquisition_prelude_plan_built_for_prelude: bool = False
    acquisition_prelude_domain_detection_error: str = ""
    windup_delayed_for_nonfeed: bool = False
    prewindup_barrier_checked: bool = False
    prewindup_barrier_required_lanes: tuple[str, ...] = ()
    prewindup_barrier_satisfied: bool = False
    prewindup_barrier_attempted_lanes: tuple[str, ...] = ()
    prewindup_barrier_skipped_lanes: dict[str, str] = field(default_factory=dict)
    prewindup_barrier_errors: dict[str, str] = field(default_factory=dict)
    prewindup_barrier_duration_s: float = 0.0
    prewindup_barrier_delayed_cycle: bool = False
    windup_guard_call_count: int = 0
    windup_guard_callback_supplied_count: int = 0
    windup_guard_callback_executed_count: int = 0
    windup_guard_last_reason: str = ""
    windup_guard_last_phase: str = ""
    windup_guard_last_allowed: bool | None = None
    windup_guard_last_callback_not_executed_reason: str = ""
    prewindup_guard_async_bridge_used: bool = False
    prewindup_guard_async_error: str = ""
    prewindup_guard_fail_closed: bool = False
    return_guard_checked: bool = False
    return_guard_required_lanes: tuple[str, ...] = ()
    return_guard_satisfied: bool = False
    return_guard_delayed_for_nonfeed: bool = False
    return_guard_block_reason: str = ""
    return_guard_attempted_lanes: tuple[str, ...] = ()
    return_guard_skipped_lanes: dict[str, str] = field(default_factory=dict)
    return_guard_errors: dict[str, str] = field(default_factory=dict)
    nonfeed_predispatch_attempted: bool = False
    nonfeed_predispatch_skipped: dict[str, str] = field(default_factory=dict)
    nonfeed_predispatch_lanes: tuple[str, ...] = ()
    nonfeed_predispatch_duration_s: float = 0.0
    windup_blocked_until_nonfeed_attempted: bool = False
    acquisition_lane_outcomes: tuple = ()
    lane_ct_accepted_findings: int = 0
    lane_wayback_accepted_findings: int = 0
    lane_pdns_accepted_findings: int = 0
    lane_blockchain_accepted_findings: int = 0
    lane_ipfs_accepted_findings: int = 0
    lane_doh_accepted_findings: int = 0
    lane_public_accepted_findings: int = 0
    doh_planned: bool = False
    doh_scheduled: bool = False
    doh_request_attempted: bool = False
    doh_domains_attempted: int = 0
    doh_raw_count: int = 0
    doh_accepted_findings: int = 0
    doh_terminal_stage: str = ""
    doh_provider_errors: tuple[str, ...] = ()
    doh_cache_used: bool = False
    doh_seed_source: str = ""
    policy_quality_feedback_calls: int = 0
    policy_quality_feedback_decisions: int = 0
    policy_quality_feedback_sources: int = 0
    policy_quality_feedback_errors: int = 0
    feed_budget_active: bool = False
    feed_budget_reason: str = ""
    feed_accepted_before_cap: int = 0
    feed_suppressed_by_budget: int = 0
    feed_budget_per_source: dict[str, int] = field(default_factory=dict)
    top_feed_source_counts: tuple[tuple[str, int], ...] = ()
    max_per_source_applied: str = ""
    nonfeed_budget_active: bool = False
    nonfeed_budget_expected_lanes: tuple[str, ...] = ()
    nonfeed_budget_terminal_lanes: tuple[str, ...] = ()
    nonfeed_budget_unresolved_lanes: tuple[str, ...] = ()
    feed_suppressed_by_nonfeed_budget: int = 0
    feed_suppression_count: int = 0
    feed_suppression_reason: str = ""
    dominant_feed_blocker: str = ""
    dominant_branch_blocker: str = ""
    branch_degradation_summary: str = ""
    dedup_preload_count: int | None = None
    dedup_preload_elapsed_s: float | None = None
    feed_zero_yield_detected: bool = False
    feed_inaccessible_detected: bool = False
    feed_content_empty_detected: bool = False
    feed_no_pattern_with_content: bool = False
    findings_build_loss_detected: bool = False
    feed_no_signal_sources: list[str] = field(default_factory=list)
    arrow_batch_hard_cap: int = 0
    arrow_batch_dropped_after_flush_failure: int = 0
    # ISSUE-3: BoundedLRUDict eviction counters for memory-bounded SprintRunContext fields.
    seen_hashes_dropped: int = 0
    entries_per_source_dropped: int = 0
    hits_per_source_dropped: int = 0
    novelty_bonuses_dropped: int = 0
    source_weights_dropped: int = 0
    feed_accepted_per_source_dropped: int = 0
    fetch_latency_ema_dropped: int = 0
    arrow_last_flush_error: str = ""
    requested_duration_s: float = 0.0
    actual_duration_s: float = 0.0
    elapsed_pct: float = 0.0
    active_window_budget_s: float = 0.0
    active_window_elapsed_s: float = 0.0
    windup_efficiency: float = 0.0
    early_exit_class: str = ""
    early_exit_reason: str = ""
    source_family_events: list[dict] = field(default_factory=list)
    MAX_SOURCE_FAMILY_EVENTS: int = 200
    nonfeed_mission_active: bool = False
    nonfeed_required_families: tuple[str, ...] = ()
    nonfeed_optional_families: tuple[str, ...] = ()
    nonfeed_family_status: dict[str, str] = field(default_factory=dict)
    nonfeed_all_required_terminal: bool = False
    nonfeed_any_accepted: bool = False
    nonfeed_provider_failures: tuple[str, ...] = ()
    nonfeed_memory_skips: tuple[str, ...] = ()
    nonfeed_mission_exit_reason: str = ""
    nonfeed_candidate_ledger_summary: dict = field(default_factory=dict)
    nonfeed_lane_eligibility: dict[str, bool] = field(default_factory=dict)
    nonfeed_doh_planner_input: list[str] = field(default_factory=list)
    nonfeed_ct_planner_candidates: list[str] = field(default_factory=list)
    nonfeed_wayback_candidates: list[str] = field(default_factory=list)
    nonfeed_passive_dns_candidates: list[str] = field(default_factory=list)
    pivot_seed_count: int = 0
    pivot_seed_type_counts: dict[str, int] = field(default_factory=dict)
    pivot_seed_sample: tuple[str, ...] = ()
    pivot_seed_domains: tuple[str, ...] = ()
    pivot_seed_ips: tuple[str, ...] = ()
    pivot_seed_urls: tuple[str, ...] = ()
    pivot_seed_hashes: tuple[str, ...] = ()
    pivot_seed_cves: tuple[str, ...] = ()
    pivot_integration_reason: str = ""
    findings: list = field(default_factory=list)
    pivot_lane_plan_count: int = 0
    planned_pivot_lanes: tuple[str, ...] = ()
    seed_quality_checked: bool = False
    seed_quality_keep_count: int = 0
    seed_quality_drop_count: int = 0
    seed_quality_drop_reasons: dict = field(default_factory=dict)
    seed_quality_kept_sample: list = field(default_factory=list)
    seed_quality_dropped_sample: list = field(default_factory=list)
    seed_quality_bypass_reason: str = ""
    seed_context_available: bool = False
    seed_context_propagated: bool = False
    lanes_unlocked_by_seed_context: list[str] = field(default_factory=list)
    seed_context_skip_reason: str = ""
    next_seeds_consumed_count: int = 0
    next_seeds_seed_source: str = ""
    next_seeds_provider_yield: bool = False
    next_seeds_pivot_deepening: bool = False
    next_seeds_query_suggestions: tuple[str, ...] = ()
    next_seeds_skip_reason: str = ""
    next_seeds_ioc_domains: tuple[str, ...] = ()
    next_seeds_ioc_ips: tuple[str, ...] = ()
    next_seeds_ioc_urls: tuple[str, ...] = ()
    next_seeds_ioc_hashes: tuple[str, ...] = ()
    next_seeds_ioc_cves: tuple[str, ...] = ()
    planner_actions_consumed_count: int = 0
    planner_action_lanes_requested: list[str] = field(default_factory=list)
    planner_action_seed_source: str = ""
    planner_action_skip_reason: str | None = None
    nonfeed_prelude_expected_lanes: tuple[str, ...] = ()
    nonfeed_prelude_attempted_lanes: tuple[str, ...] = ()
    nonfeed_prelude_terminal_lanes: tuple[str, ...] = ()
    nonfeed_prelude_missing_lanes: tuple[str, ...] = ()
    nonfeed_prelude_accepted_by_lane: dict[str, int] = field(default_factory=dict)
    nonfeed_prelude_error_by_lane: dict[str, str] = field(default_factory=dict)
    nonfeed_prelude_duration_s: float = 0.0
    nonfeed_prelude_feed_blocked_until_complete: bool = False
    pivot_graph_stats_used: bool = False
    pivot_graph_stats_keys: tuple[str, ...] = ()
    graph_aware_pivot_count: int = 0


@dataclass(slots=True)
class FeedSprintResult(SprintResult):
    """

    FEED mode result -- feed-specific telemetry fields guaranteed populated.



    Populated when FEED acquisition lane runs (structured TI feeds).

    """

    pass


@dataclass(slots=True)
class PublicSprintResult(SprintResult):
    """

    PUBLIC mode result -- public discovery pipeline fields guaranteed populated.



    Populated when PUBLIC acquisition lane runs (discovery->fetch->parse->quality->storage).

    """

    public_discovered: int = 0
    public_fetched: int = 0
    public_matched_patterns: int = 0
    public_accepted_findings: int = 0
    public_stored_findings: int = 0
    public_error: str = ""
    public_backend_degraded: bool = False
    dominant_public_blocker: str = ""
    public_terminal_stage: str = ""
    public_stage_counters: dict = field(default_factory=dict)
    public_discovery_empty_reason: str = ""
    public_provider_selection_debug: dict = field(default_factory=dict)
    wayback_attempted: bool = False
    wayback_raw_count: int = 0
    wayback_candidates_built: int = 0
    wayback_accepted_count: int = 0
    graph_rag_context_count: int = 0
    wayback_advisory_clues_count: int = 0
    wayback_changed_url_count: int = 0
    wayback_added_url_count: int = 0
    wayback_digest_changed_count: int = 0
    wayback_unchanged_rejected: int = 0
    passive_dns_attempted: bool = False
    passive_dns_raw_count: int = 0
    passive_dns_candidates_built: int = 0
    passive_dns_accepted_count: int = 0
    passive_dns_advisory_clues_count: int = 0
    passive_dns_private_ip_rejected: int = 0
    passive_dns_empty_ip_rejected: int = 0


@dataclass(slots=True)
class CtSprintResult(SprintResult):
    """

    CT mode result -- certificate transparency log pipeline fields guaranteed populated.



    Populated when CT acquisition lane runs (CT log discovery + bridge).

    """

    ct_log_discovered: int = 0
    ct_log_stored: int = 0
    ct_log_accepted_findings: int = 0
    ct_log_error: str = ""
    ct_bridge_invoked: bool = False
    ct_raw_count: int = 0
    ct_candidates_built: int = 0
    ct_storage_rejected: int = 0
    ct_storage_rejection_reasons: tuple[str, ...] = ()
    ct_quarantine_count: int = 0
    ct_quarantine_samples: tuple[str, ...] = ()
    ct_provider_status: str = ""
    ct_cache_used: bool = False
    ct_cache_stale: bool = False
    ct_cache_age_s: float = 0.0
    ct_planned: bool = False
    ct_scheduled: bool = False
    ct_provider_selected: str = ""
    ct_request_attempted: bool = False
    ct_request_timeout: bool = False
    ct_storage_attempted: bool = False
    ct_storage_accepted: bool = False
    ct_terminal_stage: str = ""
    ct_prelude_missing_but_final_attempted: bool = False
    ct_raw_domains_seen: int = 0
    ct_unique_domains_seen: int = 0
    ct_valid_public_domains: int = 0
    ct_wildcard_domains: int = 0
    ct_private_reserved_domains: int = 0
    ct_duplicate_candidates: int = 0
    ct_expansion_clues_count: int = 0
    ct_candidate_examples: tuple[str, ...] = ()
    ct_candidate_count: int = 0
    ct_valid_domain_count: int = 0
    ct_bridge_build_success_count: int = 0
    ct_bridge_quality_rejected_count: int = 0
    ct_loss_stage: str = "no_loss"
    ct_raw_sample_keys: tuple[str, ...] = ()
    ct_raw_sample_count: int = 0
    ct_bridge_rejections_count: int = 0
    ct_bridge_rejection_reasons: tuple[str, ...] = ()
    ct_candidates_accumulated: int = 0
    ct_candidates_stored: int = 0
    quality_rejection_ledger: tuple = ()
    quality_rejection_summary_by_family: dict = field(default_factory=dict)
    duplicate_rejection_summary_by_family: dict = field(default_factory=dict)
    low_information_by_family: dict = field(default_factory=dict)


@dataclass(slots=True)
class NonfeedSprintResult(SprintResult):
    """

    Nonfeed mode result -- nonfeed lane fields (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN, PIVOT).



    Populated when any nonfeed acquisition lane runs. Contains lane-specific

    telemetry for all nonfeed lanes combined.

    """

    nonfeed_plan_debug: Any = None


class PreWindupBarrierResult(msgspec.Struct, frozen=True, gc=False):
    """

    Result of a pre-windup barrier check.



    Returned by _ensure_pre_windup_lane_terminal_states() to inform

    the windup decision whether required lanes are satisfied.

    """

    required_lanes: tuple[str, ...] = ()
    satisfied: bool = False
    attempted_lanes: tuple[str, ...] = ()
    skipped_lanes: tuple[str, ...] = ()
    error_lanes: tuple[str, ...] = ()
    duration_s: float = 0.0


class SourceEconomics(msgspec.Struct, gc=False):
    """

    Per-source economics state for one sprint.



    All fields are in-memory only. Reset happens in _reset_result().

    No cross-sprint persistence. No background tasks.



    Bounded:

    - silent_streak: int (unbounded within sprint, capped by sprint length)

    - cooldown_until_cycle: int | None (None = not in cooldown)

    - recent_health_posture: str (one of hot/warm/lukewarm/marginal/cold)

    """

    source: str
    silent_streak: int = 0
    last_signal_cycle: int = -1
    cooldown_until_cycle: int | None = None
    recent_health_posture: str = "unknown"


class SourceWork(msgspec.Struct, frozen=True, gc=False):
    """A single source fetch unit."""

    feed_url: str
    source: str
    tier: SourceTier
    max_entries: int = 50


def _import_live_feed_pipeline():
    from hledac.universal.pipeline.live_feed_pipeline import FeedPipelineRunResult, async_run_live_feed_pipeline

    return (async_run_live_feed_pipeline, FeedPipelineRunResult)


def _import_live_public_pipeline():
    from hledac.universal.pipeline.live_public_pipeline import PipelineRunResult, async_run_live_public_pipeline

    return (async_run_live_public_pipeline, PipelineRunResult)


def _import_exporters():
    from hledac.universal.export import (
        render_cti_stix_bundle_to_path,
        render_diagnostic_markdown_to_path,
        render_jsonld_to_path,
        render_stix_bundle_to_path,
    )
    from hledac.universal.export.stix_exporter import collect_cti_export_inputs

    return (
        render_diagnostic_markdown_to_path,
        render_jsonld_to_path,
        render_stix_bundle_to_path,
        render_cti_stix_bundle_to_path,
        collect_cti_export_inputs,
    )


def _import_correlate_findings():
    from hledac.universal.intelligence.workflow_orchestrator import correlate_findings

    return correlate_findings


def _import_hypothesis_engine():
    from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine

    return HypothesisEngine


class PivotTask(msgspec.Struct, frozen=True, gc=False):
    """Pivot task pro agentic pivot loop -- prioritizován podle confidence * degree."""

    priority: float
    ioc_type: str
    ioc_value: str
    task_type: str


_DEDUP_LMDB_NAME = "sprint_dedup.lmdb"
_FORENSICS_LMDB_NAME = "forensics_enrichment.lmdb"
_MULTIMODAL_LMDB_NAME = "multimodal_enrichment.lmdb"


def _get_dedup_lmdb_path() -> Path:
    from hledac.universal.paths import LMDB_ROOT

    return LMDB_ROOT / _DEDUP_LMDB_NAME


def _get_forensics_lmdb_path() -> Path:
    from hledac.universal.paths import LMDB_ROOT

    return LMDB_ROOT / _FORENSICS_LMDB_NAME


def _get_multimodal_lmdb_path() -> Path:
    from hledac.universal.paths import LMDB_ROOT

    return LMDB_ROOT / _MULTIMODAL_LMDB_NAME


class SprintScheduler:
    __slots__ = (
        "_config",
        "_result",
        "_stop_requested",
        "_lane_budget_pool",
        "_lifecycle",
        "_flags",
        "_governor",
        "_backpressure_monitor",
        "_fetch_coordinator",
        "_graph_accumulator",
        "_duckdb_store",
        "_duckdb_can_ingest",
        "_duckdb_read_con",
        "_duckdb_writer_task",
        "_duckdb_writer_shutdown",
        "_duckdb_write_queue",
        "_writer_wakeup",
        "_enrichment_services",
        "_evidence_log",
        "_memory_delta_tracker",
        "_lane_rejections",
        "_lane_rejections_dropped",
        "_lane_rejections_total_seen",
        "_feed_budget_triggered",
        "_public_outcome",
        "_public_pipeline_result",
        "_public_consecutive_timeouts",
        "_public_bootstrap_enabled_at_timeout",
        "_ct_consecutive_timeouts",
        "_ct_pdns_active_done",
        "_ct_log_client",
        "_lane_outcomes",
        "_source_economics",
        "_shadow_pd_summary",
        "_hypothesis_pack_cache",
        "_pivot_planner",
        "_pivot_ioc_graph",
        "_injected_ioc_graph",
        "_planner_seed_iocs",
        "_planner_lanes",
        "_acquisition_plan",
        "_hypothesis_depth",
        "_hypothesis_query_count",
        "_enqueue_pivot",
        "_policy_manager",
        "_stealth_layer",
        "_ghost_layer",
        "_prefetch_oracle",
        "_prefetch_pipeline",
        "_temporal_predictor",
        "_security_coordinator",
        "_privacy_layer",
        "_forensics_enricher",
        "_forensics_lmdb_env",
        "_multimodal_enricher",
        "_multimodal_lmdb_env",
        "_analyst_workbench",
        "_communication_layer",
        "_ioc_scorer",
        "_ioc_cooccurrence_miner",
        "_ioc_cooccurrence_miner",
        "_tor_transport",
        "_i2p_transport",
        "_nym_transport",
        "_dht_node",
        "_advisory_gate_snapshot",
        "_arrow_last_flush",
        "_branch_value_summary",
        "_correlation_cache",
        "_dedup_dirty",
        "_dedup_env",
        "_dedup_seen",
        "_dedup_loading_task",
        "_seen_hashes",
        "_recent_iocs",
        "_dedup_rust",
        "_dedup_python_fallback",
        "_dedup_mode",
        "_prewindup_barrier_delayed",
        "_barrier_retry_count",
        "_nonfeed_predispatch_done",
        "_nonfeed_ledger",
        "_synth_windup_task",
        "_bg_tasks",
        "_sidecar_tasks",
        "_sidecars_skipped",
        "_sidecar_orchestrator",
        "_memory_manager",
        "_layer_manager",
        "_target_memory_service",
        "_metrics_registry",
        "_metrics_initialized",
        "_hermes_engine",
        "_wall_clock_start",
        "_last_cycle_start",
        "_last_ooda",
        "_last_sources",
        "_last_partial_finding_count",
        "_last_speculative",
        "_cycle_time_ema",
        "_effective_max_cycles",
        "_hard_deadline_monotonic",
        "_prev_chain_hash",
        "_rel_discovery_engine",
        "_run_exit_path_override",
        "_runner",
        "_lc_adapter",
        "_run_started_at",
        "_hermes_prewarm_task",
        "_hermes_prewarm_exception",
        "_barrier_import_done",
        "_doh_adapter",
        "_fetch_semaphore",
        "_query",
        "_privacy_context_id",
        "_sprint_id",
        "_sprint_depth",
        "_entries_per_source",
        "_hits_per_source",
        "_cycle_timeout_count",
        "_source_weights",
        "_novelty_bonuses",
        "_source_quality_feedback",
        "_feed_accepted_per_source",
        "_pending_extractions",
        "_extraction_drain_count",
        "_extraction_drain_deadline_s",
        "_pivot_queue",
        "_pivot_stats",
        "_speculative_results",
        "_speculative_dns_cache",
        "_ooda_interval",
        "_fetch_latency_ema",
        "_fetch_latency_ema_order",
        "_MAX_FETCH_LATENCY_EMA",
        "_hard_deadline_checked_count",
        "_ARROW_FLUSH_N",
        "_ARROW_FLUSH_S",
        "_ARROW_BATCH_HARD_CAP",
        "_MAX_FINDINGS_PER_SPRINT",
        "_arrow_batch_dropped_after_flush_failure",
        "_arrow_last_flush_error",
        "_finding_count",
        "_synthesis_engine",
        "_synthesis_runner",
        "_pivot_rewards",
        "_ioc_graph",
        "_all_findings",
        "_lane_verdicts",
        "_feed_verdicts",
        "_public_verdicts",
        "_planned_pivots",
        "_analyst_brief",
        "_peak_rss_gib",
        "_timer",
        "_health_cache",
        "sprint_id",
    )
    "\n\n    Tier-aware sprint scheduler sidecar.\n\n\n\n    Runs bounded feed-fetch cycles under a SprintLifecycleManager.\n\n    Does NOT own the lifecycle -- lifecycle is passed in and owned by caller.\n\n\n\n    Authority boundaries (Sprint F350M §H5):\n\n    - Does NOT execute tools via execute_with_limits()\n\n    - Does NOT activate providers via acquire() or load_model()\n\n    - Does NOT create new persistent state beyond in-sprint accumulators\n\n    - Does NOT own lifecycle phase transitions\n\n    - Does NOT dispatch work based on shadow pre-decision output\n\n\n\n    Runtime mode semantics (Sprint F350M §H1-H2):\n\n    - legacy_runtime (default): normal scheduler path -- full execution\n\n    - scheduler_shadow: read-only diagnostic path -- consume_shadow_pre_decision() only\n\n    - scheduler_active: NOT supported -- any implied readiness is FALSE.\n\n      Fallback: diagnostic-only containement. Activation requires separate verified sprint.\n\n\n\n    Advisory gate: computed at WINDUP entry, DIAGNOSTIC ONLY.\n\n    Shadow pre-decision: read-only parity/composition, DIAGNOSTIC ONLY.\n\n\n\n    Dependency injection: see inject_* methods for authoritative documentation.\n\n    "

    def __init__(
        self, config: SprintSchedulerConfig, ct_log_client: Any = None, flags: Any = None, *, ioc_graph: Any = None
    ) -> None:
        self._timer: SprintTimer = SprintTimer()
        self._health_cache: tuple[HealthReport, str] | None = None
        self._init_core_state(config, flags)
        self._init_dedup_and_lifecycle(ct_log_client)
        self._init_duckdb_pipeline()
        self._init_source_tracking()
        self._init_pending_extractions()
        self._init_pivot_state()
        self._init_background_tasks()
        self._init_fetch_latency_ema()
        self._init_arrow_and_synthesis()
        self._init_hermes_engine()
        self._init_fetch_coordinator()
        self._init_findings_and_prefetch()
        self._init_graph_and_ioc_state(ioc_graph)
        self._init_layers()
        self._init_planner_and_advisory()
        self._init_target_and_metrics()

    async def aclose(self, timeout_s: float = 10.0) -> None:
        """F285: Canonical async cleanup — call on SIGINT / windup / completion.

        Args:
            timeout_s: max seconds for each cleanup phase (default 10.0).
                       Individual phases (DuckDB writer, LMDB, Hermes, transports)
                       have their own bounded timeouts (5s / 5s / 5s).

        Addresses M1 8GB resource pressure: Metal cache, LMDB envs, DuckDB
        writer, Hermes engine, transport adapters, and metrics registry are all
        explicitly released here rather than relying on GC.

        Call sites (priority order):
          1. core/__main__.py finally: await scheduler.aclose()
          2. Soft-fail path: await scheduler.aclose()
          3. Any caller that creates SprintScheduler and needs deterministic cleanup.

        Ordering rationale:
          - DuckDB writer FIRST (drains pending writes)
          - LMDB envs SECOND (flushes write buffers)
          - Hermes / Metal THIRD (releases GPU memory on M1)
          - Transport adapters LAST (Tor, I2P, Nym, DHT, Gopher)
          - Metrics registry FINAL (flushes telemetry)

        Fail-safe: every step is wrapped in try/except so one failure never
        prevents subsequent steps from running.
        """
        import asyncio
        import time as _time

        _log = get_logger(__name__)
        self._health_cache = None
        _start = _time.monotonic()
        _sid = getattr(self, "sprint_id", "unknown") or "unknown"
        _writer_shutdown_set = False
        _writer_task = getattr(self, "_duckdb_writer_task", None)
        _shutdown_evt = getattr(self, "_duckdb_writer_shutdown", None)
        if _shutdown_evt is not None and (not _shutdown_evt.is_set()):
            _shutdown_evt.set()
            _writer_wakeup = getattr(self, "_writer_wakeup", None)
            if _writer_wakeup is not None:
                _writer_wakeup.set()
            _writer_shutdown_set = True
            _log.info("[aclean] DuckDB writer shutdown signalled")
        if _writer_task is not None and (not _writer_task.done()):
            try:
                await safe_wait_for(_writer_task, timeout=5.0, label="_writer_task")
                _log.info("[aclean] DuckDB writer task completed gracefully")
            except TimeoutError:
                _log.info("[aclean] DuckDB writer task did not complete in 5s — cancelling")
                _writer_task.cancel()
                try:
                    await _writer_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
            except Exception as _e:  # noqa: BLE001 — best-effort; export/write failure; non-critical
                _log.info("[aclean] DuckDB writer task error: %s", _e)
        _lmdb_close_errors: list[str] = []
        for _attr in ("_dedup_env", "_forensics_lmdb_env", "_multimodal_lmdb_env"):
            _env = getattr(self, _attr, None)
            if _env is None:
                continue
            try:
                if hasattr(_env, "flush"):
                    _env.flush()
                _env.close()
                _log.info("[aclean] %s closed", _attr)
            except Exception as _e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                _lmdb_close_errors.append(f"{_attr}: {_e}")
                _log.info("[aclean] %s close error: %s", _attr, _e)
            finally:
                setattr(self, _attr, None)
        _db_con = getattr(self, "_duckdb_read_con", None)
        if _db_con is not None:
            try:
                _db_con.close()
                _log.info("[aclean] DuckDB read connection closed")
            except Exception as _e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                _log.info("[aclean] DuckDB read connection close error: %s", _e)
            finally:
                self._duckdb_read_con = None
        _store = getattr(self, "_duckdb_store", None)
        if _store is not None and hasattr(_store, "aclose"):
            try:
                await safe_wait_for(_store.aclose(), timeout=10.0, label="_store")
                _log.info("[aclean] DuckDB store closed")
            except TimeoutError:
                _log.info("[aclean] DuckDB store aclose() timed out")
            except Exception as _e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                _log.info("[aclean] DuckDB store aclose() error: %s", _e)
        _hermes = getattr(self, "_hermes_engine", None)
        if _hermes is not None:
            try:
                if hasattr(_hermes, "aclose"):
                    await safe_wait_for(_hermes.aclose(), timeout=10.0, label="_hermes")
                    _log.info("[aclean] Hermes engine closed")
                elif hasattr(_hermes, "unload"):
                    _hermes.unload()
                    _log.info("[aclean] Hermes engine unloaded")
            except TimeoutError:
                _log.info("[aclean] Hermes engine close timed out")
            except Exception as _e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                _log.info("[aclean] Hermes engine close error: %s", _e)
            finally:
                self._hermes_engine = None
        _metrics = getattr(self, "_metrics_registry", None)
        if _metrics is not None:
            try:
                if hasattr(_metrics, "flush"):
                    _metrics.flush()
                if hasattr(_metrics, "close"):
                    _metrics.close()
                _log.info("[aclean] metrics registry flushed/closed")
            except Exception as _e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                _log.info("[aclean] metrics registry error: %s", _e)
        for _attr, _stop_attr in (
            ("_tor_transport", "_tor_transport"),
            ("_i2p_transport", "_i2p_transport"),
            ("_nym_transport", "_nym_transport"),
            ("_dht_node", "_dht_node"),
        ):
            _transport = getattr(self, _attr, None)
            if _transport is None:
                continue
            try:
                if hasattr(_transport, "stop"):
                    await safe_wait_for(_transport.stop(), timeout=5.0, label="_transport_stop")
                elif hasattr(_transport, "aclose"):
                    await safe_wait_for(_transport.aclose(), timeout=5.0, label="_transport_aclose")
                _log.info("[aclean] %s stopped", _attr)
            except TimeoutError:
                _log.info("[aclean] %s stop timed out", _attr)
            except asyncio.CancelledError:
                pass
            except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                _log.info("[aclean] %s stop error: %s", _attr, _e)
            finally:
                setattr(self, _attr, None)
        _enrich = getattr(self, "_enrichment_services", None)
        if _enrich is not None:
            try:
                if hasattr(_enrich, "close"):
                    await safe_wait_for(_enrich.close(), timeout=5.0, label="_enrich")
                    _log.debug("[aclean] enrichment_services closed")
            except TimeoutError:
                _log.debug("[aclean] enrichment_services close timed out")
            except Exception as _e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                _log.debug("[aclean] enrichment_services close error: %s", _e)
            finally:
                self._enrichment_services = None
        _elog = getattr(self, "_evidence_log", None)
        if _elog is not None:
            try:
                if hasattr(_elog, "aclose"):
                    await safe_wait_for(_elog.aclose(), timeout=5.0, label="_elog")
                elif hasattr(_elog, "close"):
                    _elog.close()
                _log.debug("[aclean] evidence_log closed")
            except TimeoutError:
                _log.debug("[aclean] evidence_log close timed out")
            except Exception as _e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                _log.debug("[aclean] evidence_log close error: %s", _e)
            finally:
                self._evidence_log = None
        _ghost = getattr(self, "_ghost_layer", None)
        if _ghost is not None:
            try:
                if hasattr(_ghost, "aclose"):
                    await safe_wait_for(_ghost.aclose(), timeout=5.0, label="_ghost")
                elif hasattr(_ghost, "unmount"):
                    _ghost.unmount()
                _log.debug("[aclean] ghost_layer closed")
            except TimeoutError:
                _log.debug("[aclean] ghost_layer close timed out")
            except Exception as _e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                _log.debug("[aclean] ghost_layer close error: %s", _e)
            finally:
                self._ghost_layer = None
        self._seen_hashes.clear()
        self._entries_per_source.clear()
        self._hits_per_source.clear()
        _elapsed = _time.monotonic() - _start
        _log.info("[aclean] %s done in %.2fs (lmdb_errors=%s)", _sid, _elapsed, len(_lmdb_close_errors))

    def _init_core_state(self, config: SprintSchedulerConfig, flags: Any) -> None:
        """Phase A: Core config and basic state (13 attrs)."""
        self._config = config
        # ISSUE-3: BoundedLRUDict replaces unbounded dict — LRU eviction with drop counter
        self._seen_hashes: BoundedLRUDict = BoundedLRUDict(maxsize=DEFAULT_SEEN_HASHES_MAXSIZE)
        self._entries_per_source: BoundedLRUDict = BoundedLRUDict(maxsize=DEFAULT_ENTRIES_PER_SOURCE_MAXSIZE)
        self._hits_per_source: BoundedLRUDict = BoundedLRUDict(maxsize=DEFAULT_ENTRIES_PER_SOURCE_MAXSIZE)
        self._result = SprintSchedulerResult()
        self._lane_budget_pool = LaneBudgetPool()
        self._stop_requested = False
        self._sprint_depth: int = 0
        self._cycle_timeout_count: int = 0
        self._nonfeed_ledger: NonfeedCandidateLedger = NonfeedCandidateLedger()
        self._lifecycle = None
        self._query: str = ""
        self._lc_adapter: _LifecycleAdapter | None = None
        self._flags = flags

    def _init_dedup_and_lifecycle(self, ct_log_client: Any) -> None:
        """Phase B: Persistent dedup, lifecycle adapter, IOC-aware scoring (7 attrs)."""
        from core.rust_backend import rust

        self._dedup_rust = rust.bloom.BloomFilter(capacity=10000000)
        self._dedup_python_fallback: set[str] = set()
        self._dedup_mode: str = "rust" if rust.is_available else "python"
        self._dedup_env: lmdb.Environment | None = None
        self._dedup_seen: set[str] = set()
        self._dedup_dirty: bool = False
        self._dedup_loading_task: asyncio.Task | None = None
        self._source_weights: dict[str, float] = {}
        self._novelty_bonuses: dict[str, float] = {}
        self._ct_log_client: Any = ct_log_client
        self._policy_manager: Any = None

    def _init_duckdb_pipeline(self) -> None:
        """Phase C: DuckDB write pipeline — producer-consumer queue (5 attrs)."""
        self._duckdb_write_queue: asyncio.Queue[tuple[Any, list, str]] = asyncio.Queue(maxsize=32)
        self._duckdb_writer_task: asyncio.Task | None = None
        self._duckdb_writer_shutdown: asyncio.Event | None = None
        self._writer_wakeup: asyncio.Event = asyncio.Event()
        self._duckdb_store: Any = None
        self._duckdb_read_con: Any | None = None
        self._duckdb_can_ingest: bool = False

    def _init_source_tracking(self) -> None:
        """Phase D: Source quality feedback and feed dominance tracking (4 attrs)."""
        self._source_quality_feedback: dict[str, dict[str, int]] = {}
        self._feed_accepted_per_source: dict[str, int] = {}
        self._feed_budget_triggered: bool = False
        self._source_economics: dict[str, SourceEconomics] = {}

    def _init_pending_extractions(self) -> None:
        """Phase E: In-flight pattern-extraction tracker — F273C bounded ring (3 attrs)."""
        from collections import deque as _deque_f273

        self._pending_extractions: _deque_f273 = _deque_f273(maxlen=512)
        self._extraction_drain_count: int = 0
        self._extraction_drain_deadline_s: float = 30.0

    def _init_pivot_state(self) -> None:
        """Phase F: Agentic pivot loop — queue, stats, hypothesis tracking (12 attrs)."""
        self._pivot_queue: asyncio.PriorityQueue[tuple[float, str, PivotTask]] = asyncio.PriorityQueue(maxsize=200)
        self._last_sources: list[str] = []
        self._pivot_stats: dict[str, int] = {"total": 0, "processed": 0, "errors": 0}
        self._pivot_ioc_graph: Any = None
        self._hypothesis_depth: int = 0
        self._hypothesis_query_count: int = 0
        self._pivot_rewards: dict[str, list[float]] = {}
        # ISSUE-6: recent_iocs now lives in SprintRunContext (deque maxlen=200).
        # self._recent_iocs is removed — all access via get_sprint_ctx().recent_iocs.
        self._planned_pivots: list = []
        self._pivot_planner: Any = None
        self._enqueue_pivot: Any = None

    def _init_background_tasks(self) -> None:
        """Phase G: Background tasks, speculative results, OODA loop (13 attrs)."""
        self._bg_tasks: set[asyncio.Task] = set()
        self._synth_windup_task: asyncio.Task | None = None
        self._sidecar_tasks: set[asyncio.Task] = set()
        self._speculative_results: dict[str, object] = {}
        self._speculative_dns_cache: dict[str, list[str]] = {}
        self._last_speculative: float = 0.0
        self._ooda_interval: float = 60.0
        self._last_ooda: float = 0.0
        self._nonfeed_predispatch_done: bool = False
        self._prewindup_barrier_delayed: bool = False
        self._graph_accumulator = None
        self._communication_layer: Any = None
        self._stealth_layer: Any = None
        self._ghost_layer: Any = None

    def _init_fetch_latency_ema(self) -> None:
        """Phase H: Adaptive timeout EMA — per-domain latency learning (3 attrs)."""
        self._fetch_latency_ema: dict[str, float] = {}
        self._fetch_latency_ema_order: deque[str] = deque(maxlen=1000)
        self._MAX_FETCH_LATENCY_EMA: int = 1000

    def _init_arrow_and_synthesis(self) -> None:
        """Phase I: Arrow columnar buffer, synthesis, enrichment, evidence, chain (15 attrs)."""
        self._arrow_last_flush: float = 0.0
        self._wall_clock_start: float = 0.0
        self._hard_deadline_monotonic: float | None = None
        self._hard_deadline_checked_count: int = 0
        self._ARROW_FLUSH_N: int = 1000
        self._ARROW_FLUSH_S: float = 60.0
        self._ARROW_BATCH_HARD_CAP: int = self._resolve_arrow_batch_hard_cap()
        self._MAX_FINDINGS_PER_SPRINT: int = 500
        self._arrow_batch_dropped_after_flush_failure: int = 0
        self._arrow_last_flush_error: str | None = None
        self._finding_count: int = 0
        self._last_partial_finding_count: int = 0
        self._synthesis_engine: str = "unknown"
        self._synthesis_runner: Any = None
        self._ioc_scorer: Any = None
        self._enrichment_services: Any = None
        self._evidence_log: Any = None
        self._prev_chain_hash: str | None = None

    def _init_hermes_engine(self) -> None:
        """Phase J: Hermes engine, memory manager, M1 governor, fetch semaphore (5 attrs)."""
        self._hermes_engine: Any = None
        self._memory_manager: Any = None
        self._governor = None
        self._fetch_semaphore: asyncio.Semaphore = asyncio.Semaphore(20)
        self.sprint_id: str = ""

    def _init_fetch_coordinator(self) -> None:
        """Phase K: FetchCoordinator instantiation with provider lambdas + DNS prewarm."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator as _FC

        self._fetch_coordinator = _FC(
            max_concurrent=20,
            pivot_queue_provider=lambda: getattr(self, "_pivot_queue", None),
            pivot_stats_provider=lambda: getattr(self, "_pivot_stats", {}) or {},
            hypothesis_query_count_provider=lambda: getattr(self, "_hypothesis_query_count", 0),
            hypothesis_query_count_setter=lambda v: setattr(self, "_hypothesis_query_count", v),
            hypothesis_depth_provider=lambda: getattr(self, "_hypothesis_depth", 0),
            hypothesis_depth_setter=lambda v: setattr(self, "_hypothesis_depth", v),
            sprint_config_provider=lambda: self._config,
            adaptive_priority_provider=lambda tt, base: self._get_adaptive_priority(tt, base),
            enqueue_pivot_provider=lambda **kw: self.enqueue_pivot(**kw),
            concurrency_provider=lambda _mon=getattr(self, "_backpressure_monitor", None): (
                _mon.backpressure_provider() if _mon is not None else None
            ),
        )
        try:
            resolver = get_batch_dns_resolver()
            try:
                _loop = asyncio.get_running_loop()
                asyncio.run_coroutine_threadsafe(resolver.prewarm(), _loop)
            except RuntimeError:
                import threading

                _ev = threading.Event()

                def _run_prewarm() -> None:
                    try:
                        with asyncio.Runner() as _runner:
                            asyncio.set_event_loop(_runner.get_loop())
                            _runner.get_loop().run_until_complete(resolver.prewarm())
                    finally:
                        _ev.set()

                _t = threading.Thread(target=_run_prewarm, daemon=True, name="batch_dns_prewarm")
                _t.start()
        except Exception as _exc:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
            log.debug("[F2.3] BatchDNS prewarm init failed (non-critical): %s", _exc)

    def _init_findings_and_prefetch(self) -> None:
        """Phase L: All findings, prefetch oracle, temporal predictor, correlation cache (10 attrs)."""
        self._all_findings: list[dict] = []
        self._prefetch_oracle: Any = None
        self._prefetch_pipeline: Any = None
        self._temporal_predictor: Any = None
        self._shadow_pd_summary: Any = None
        self._advisory_gate_snapshot: Any = None
        self._correlation_cache: dict | None = None
        self._hypothesis_pack_cache: dict | None = None
        self._branch_value_summary: dict | None = None
        self._acquisition_plan: Any = None
        self._ioc_cooccurrence_miner: Any = None

    def _init_graph_and_ioc_state(self, ioc_graph: Any = None) -> None:
        """Phase M: Graph accumulator, IOC graph, lane outcomes, verdict accumulators (21 attrs)."""
        self._lane_outcomes: tuple = ()
        self._lane_rejections: list[dict] = []
        self._planner_seed_iocs: dict[str, tuple[str, ...]] = {}
        self._planner_lanes: list[str] = []
        self._lane_rejections_total_seen: int = 0
        self._lane_rejections_dropped: int = 0
        self._lane_verdicts: list[tuple[str, int, int, int, int]] = []
        self._feed_verdicts: list[tuple[str, int, int, int, int]] = []
        self._public_verdicts: list[dict] = []
        self._public_outcome: dict | None = None
        self._public_pipeline_result: Any | None = None
        self._layer_manager: Any = None
        self._privacy_layer: Any = None
        self._security_coordinator: Any = None
        self._privacy_context_id: str | None = None
        self._dht_node: Any = None
        self._i2p_transport: Any = None
        self._nym_transport: Any = None
        self._tor_transport: Any = None
        self._ioc_graph: Any = None
        self._injected_ioc_graph: Any = ioc_graph
        self._rel_discovery_engine: Any = None

    def _init_layers(self) -> None:
        """Phase N: Privacy, stealth, ghost layers + DOH adapter + circuit breakers (3 attrs)."""
        self._doh_adapter: Any = None
        self._public_consecutive_timeouts: int = 0
        self._ct_consecutive_timeouts: int = 0

    def _init_planner_and_advisory(self) -> None:
        """Phase O: Pivot planner and advisory state (5 attrs)."""
        self._target_memory_service: TargetMemoryService | None = None
        self._analyst_brief: Any = None
        self._sidecars_skipped: set[str] = set()
        self._peak_rss_gib: float = 0.0
        self._hermes_prewarm_exception: Any = None
        self._memory_delta_tracker = get_memory_delta_tracker()

    def _init_target_and_metrics(self) -> None:
        """Phase P: Target memory service, analyst workbench, metrics registry (2 attrs)."""
        self._metrics_registry: Any = None
        self._metrics_initialized: bool = False
        self._timer: SprintTimer = SprintTimer()

    def _update_source_economics(self, feed_url: str, result: Any, current_cycle: int) -> None:
        """

        Update per-source economics from pipeline result signals.



        Uses only existing surfaces from FeedPipelineRunResult:

        - signal_stage: cold/hot diagnosis

        - feed_confidence_score: 0-100 adapter-informed confidence

        - winning_source_breakdown: signal origin analysis



        Economics state is in-memory only for the current sprint.

        Reset happens in _reset_result().

        """
        econ = self._source_economics.setdefault(feed_url, SourceEconomics(source=feed_url))
        _tel = getattr(result, "telemetry", None)
        signal_stage = getattr(_tel, "signal_stage", "unknown") or "unknown" if _tel else "unknown"
        feed_conf = getattr(_tel, "feed_confidence_score", 0) or 0 if _tel else 0
        winning = getattr(_tel, "winning_source_breakdown", {}) or {} if _tel else {}
        cold_stages = {"empty_registry", "no_pattern_hits", "content_empty"}
        is_cold = signal_stage in cold_stages or feed_conf == 0
        match ():
            case _ if signal_stage == "prestore_findings_present":
                econ.recent_health_posture = "hot"
                econ.last_signal_cycle = current_cycle
                econ.silent_streak = 0
                econ.cooldown_until_cycle = None
            case _ if feed_conf >= 60:
                econ.recent_health_posture = "warm"
                econ.last_signal_cycle = current_cycle
                econ.silent_streak = 0
                econ.cooldown_until_cycle = None
            case _ if feed_conf >= 20:
                econ.recent_health_posture = "lukewarm"
                econ.silent_streak = econ.silent_streak + 1 if econ.silent_streak > 0 else 1
                if econ.cooldown_until_cycle is None and econ.silent_streak >= 2:
                    econ.cooldown_until_cycle = current_cycle + 3
            case _ if is_cold:
                econ.recent_health_posture = "cold"
                if econ.cooldown_until_cycle is None:
                    econ.silent_streak += 1
                    if econ.silent_streak >= 2:
                        econ.cooldown_until_cycle = current_cycle + 3
            case _:
                econ.recent_health_posture = "marginal"
                econ.silent_streak = econ.silent_streak + 1 if econ.silent_streak > 0 else 1
        feed_native_hits = winning.get("feed_native", 0)
        fallback_hits = winning.get("fallback", 0)
        if feed_native_hits > fallback_hits * 2 and feed_native_hits > 0:
            econ.recent_health_posture = "hot"

    def _get_source_economics(self, feed_url: str) -> SourceEconomics | None:
        """Return economics state for a source, or None if not yet seen."""
        return self._source_economics.get(feed_url)

    def _is_source_in_cooldown(self, feed_url: str, current_cycle: int) -> bool:
        """True if source is in bounded cooldown and cycle hasn't exceeded it."""
        econ = self._source_economics.get(feed_url)
        if econ is None:
            return False
        if econ.cooldown_until_cycle is None:
            return False
        return current_cycle < econ.cooldown_until_cycle

    def _should_deprioritize_source(self, feed_url: str, current_cycle: int) -> bool:
        """

        Return True if source should be deprioritized this cycle.



        Deprioritization conditions (all bounded, all in-memory):

        1. Source is in cooldown -- pushed to end of work list

        2. Silent streak >= 4 cycles -- deprioritized but NOT excluded

        """
        econ = self._source_economics.get(feed_url)
        if econ is None:
            return False
        if self._is_source_in_cooldown(feed_url, current_cycle):
            return True
        if econ.silent_streak >= 4:
            return True
        return False

    def _sort_work_items_by_economics(self, items: list[SourceWork], current_cycle: int) -> list[SourceWork]:
        """

        Re-sort work items by source economics.



        Order:

        1. Sources NOT in cooldown first (natural priority)

        2. Sources with hot/warm posture boosted

        3. Cold/in-cooldown sources at the end

        4. Tier ordering still applies as secondary sort key

        5. F200A: Advisory prefetch oracle score multiplies the sort key



        F200A: oracle is ADVISORY ONLY -- scheduler retains authority.

        If oracle is None or suggest_scores fails -> falls back to default ordering.

        """

        def economics_sort_key(item: SourceWork) -> tuple:
            econ = self._source_economics.get(item.feed_url)
            tier_order = _TIER_ORDER.index(item.tier)
            if econ is None:
                return (0, tier_order, 0, item.feed_url)
            in_cooldown = self._is_source_in_cooldown(item.feed_url, current_cycle)
            streak = econ.silent_streak
            posture_score = {"hot": 0, "warm": 1, "lukewarm": 2, "marginal": 3, "cold": 4}.get(
                econ.recent_health_posture, 5
            )
            if in_cooldown:
                return (tier_order, 5, streak, item.feed_url)
            return (tier_order, posture_score, streak, item.feed_url)

        oracle_scores: dict[str, float] = {}
        if self._prefetch_oracle is not None:
            try:
                oracle_scores = self._prefetch_oracle.suggest_scores(items, current_cycle)
            except Exception as _exc:  # noqa: BLE001 — best-effort; prefetch/oracle failure; non-critical
                log.debug("prefetch_oracle.suggest_scores failed: %s", _exc)
                oracle_scores = {}

        def oracle_sort_key(item: SourceWork) -> tuple:
            base_key = economics_sort_key(item)
            oracle_mult = oracle_scores.get(item.feed_url, 1.0)
            oracle_shift = (oracle_mult - 1.0) * 10
            return (base_key[0], base_key[1], base_key[2] - oracle_shift, base_key[3])

        return sorted(items, key=oracle_sort_key)

    def record_pivot_outcome(self, task_type: str, found_count: int, elapsed_s: float) -> None:
        """

        Zaznamenej výsledek pivot tasku jako reward signal pro RL.

        reward = findings per second (FPS) -- normalizovaný na [0, 1].

        """
        import math

        if elapsed_s <= 0:
            return
        fps = found_count / elapsed_s
        reward = min(1.0, math.log1p(fps) / math.log1p(10))
        history = self._pivot_rewards.setdefault(task_type, [])
        history.append(reward)
        if len(history) > 20:
            self._pivot_rewards[task_type] = history[-20:]

    async def record_hypothesis_feedback(
        self, pivot_type: str, ioc_type: str, produced_count: int, accepted_count: int, signal_value: float
    ) -> None:
        """

        F203G: Record hypothesis feedback to DuckDB for future pivot planning.



        Persists a HypothesisFeedbackRecord to the duckdb_store for aggregation

        and use by PivotPlanner to penalize low-yield pivot types.



        Silently fails if duckdb_store is unavailable.



        Args:

            pivot_type: domain/identity/leak/archive/graph

            ioc_type: The IOC type operated on

            produced_count: Number of findings produced by this pivot

            accepted_count: Number of findings accepted (stored)

            signal_value: reward signal [0.0, 1.0]

        """
        store = getattr(self, "_duckdb_store", None)
        if store is None:
            return
        try:
            import time as _time
            import uuid

            from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackRecord

            record = HypothesisFeedbackRecord(
                id=str(uuid.uuid4()),
                target_id=getattr(self, "sprint_id", "") or "default",
                pivot_type=pivot_type,
                ioc_type=ioc_type,
                produced_count=produced_count,
                accepted_count=accepted_count,
                signal_value=signal_value,
                ts=_time.time(),
            )
            await store.async_record_hypothesis_feedback(record)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass

    def _check_hard_deadline(self) -> bool:
        """

        Check if the hard monotonic deadline has been exceeded.



        Returns:

            True if deadline is NOT exceeded (new work may proceed).

            False if deadline IS exceeded (no new branch dispatch).



        This method is idempotent -- it can be called multiple times per cycle

        without changing state. Deadline-exceeded state is tracked once in

        the result and never reset.

        """
        if self._hard_deadline_monotonic is None:
            return True
        self._result.hard_deadline_checked_count += 1
        if self._result.hard_deadline_exceeded:
            return False
        elapsed = _time.monotonic() - self._hard_deadline_monotonic
        if elapsed >= 0.0:
            self._result.hard_deadline_exceeded = True
            self._result.hard_deadline_exceeded_at_cycle = self._result.cycles_started
            self._result.hard_deadline_remaining_s_at_exit = 0.0
            log.warning(
                f"[F212A] Hard deadline exceeded at cycle {self._result.cycles_started}. Elapsed={elapsed:.1f}s. Stopping new work."
            )
            if self._result.cycles_started == 0:
                _adapter = self._lc_adapter
                if _adapter is not None:
                    _adapter.set_deadline_expired_pre_cycle()
                elif hasattr(self._lifecycle, "set_deadline_expired_pre_cycle"):
                    self._lifecycle.set_deadline_expired_pre_cycle()
            return False
        return True

    def _get_adaptive_priority(self, task_type: str, base_priority: float = 0.5) -> float:
        """

        Vrátí EMA reward jako priority modifikátor.

        Task types s vyšší historickou yield dostávají vyšší prioritu.

        """
        history = self._pivot_rewards.get(task_type, [])
        if not history:
            return base_priority
        ema = history[0]
        for r in history[1:]:
            ema = 0.3 * r + 0.7 * ema
        return round(0.7 * ema + 0.3 * base_priority, 4)

    async def _initialize_sprint_run(
        self,
        adapter: Any,
        lifecycle: Any,
        ct_log_client: Any | None,
        policy_manager: Any,
        duckdb_store: Any,
        now_monotonic: float | None,
        query: str = "",
    ) -> tuple[float, bool, Any | None]:
        """Phase 1: Sprint initialization - privacy, governor, sidecar, layers, CT, dedup."""
        self._barrier_import_done: asyncio.Event = asyncio.Event()
        try:
            from hledac.universal.core.protocols import get_governor

            self._governor = get_governor()
        except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning("failed to initialize M1 resource governor: %s", _exc)
            self._governor = None
        self._backpressure_monitor = None
        if self._governor is not None:
            try:
                from hledac.universal.coordinators.backpressure import BackpressureMonitor

                self._backpressure_monitor = BackpressureMonitor(self._governor, min_clearnet=1, max_clearnet=25)
                log.info("[BACKPRESSURE] monitor initialized (clearnet_max=25, min=1)")
            except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.warning("failed to initialize backpressure monitor: %s", _exc)

        # ISSUE #23: wire HTTP cache before any httpx sessions are created
        # FetchCoordinator._do_initialize() calls build_cache_transport() + set_httpx_cache_transport()
        if getattr(self, "_fetch_coordinator", None) is not None:
            try:
                await self._fetch_coordinator.initialize()
            except Exception as _exc:  # noqa: BLE001 — best-effort; memory operation; non-critical
                log.warning("failed to initialize FetchCoordinator: %s", _exc)
        from hledac.universal.utils.async_helpers import safe_create_task

        def _prewarm_mlx_sync() -> None:
            """Single unified prewarm — loads MLXEmbeddingManager singleton (shared by ModernBertEngine).

            F320: Skip if prewarm_daemon already loaded models at startup.
            is_prewarm_done() is checked first to avoid redundant ~10-15s load.
            """
            try:
                from hledac.universal.runtime.prewarm_daemon import is_prewarm_done

                if is_prewarm_done():
                    log.debug("[mlx_embed_prewarm] skipped — prewarm_daemon already loaded")
                    return
                from hledac.universal.compat.core_mlx_embeddings import get_embedding_manager

                mgr = get_embedding_manager()
                if mgr is not None and (not mgr._is_loaded):
                    with asyncio.Runner() as _runner:
                        result = try_op(
                            lambda: _runner.get_loop().run_until_complete(mgr._load_model()), label="mlx_embed_prewarm"
                        )
                        if result.is_err():
                            log.debug("[mlx_embed_prewarm] skipped: %s", getattr(result, "error", result))
            except Exception:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
                pass

        safe_create_task(asyncio.to_thread(_prewarm_mlx_sync), name="mlx_embed_prewarm")

        def _prewarm_hermes_sync() -> None:
            """Sync wrapper: runs async _prewarm_hermes_for_sprint in dedicated thread.

            F320: Skip if prewarm_daemon already loaded Hermes at startup.
            """
            try:
                from hledac.universal.runtime.prewarm_daemon import is_prewarm_done

                if is_prewarm_done():
                    log.debug("[hermes_prewarm] skipped — prewarm_daemon already loaded")
                    return
                with asyncio.Runner() as _runner:
                    _runner.get_loop().run_until_complete(self._prewarm_hermes_for_sprint())
            except Exception:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
                pass

        self._hermes_prewarm_task: asyncio.Future[Any] | asyncio.Task[Any] | None = safe_create_task(
            asyncio.to_thread(_prewarm_hermes_sync), name="hermes_prewarm_phase1"
        )

        def _prewarm_patterns_sync() -> None:
            try:
                from hledac.universal.utils.patterns.pattern_matcher import prewarm

                prewarm()
            except Exception:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
                pass

        safe_create_task(asyncio.to_thread(_prewarm_patterns_sync), name="pattern_prewarm")
        from hledac.universal.runtime.sidecar_orchestrator import SidecarOrchestrator

        self._sidecar_orchestrator = SidecarOrchestrator(self._result, governor=self._governor, scheduler=self)
        if ENV.get_bool("HLEDAC_ENABLE_LAYERS"):
            try:
                from hledac.universal.layers.layer_manager import LayerManager

                self._layer_manager = LayerManager(config=None)
                log.info("layers LayerManager initialized")
                try:
                    _privacy = self._privacy_layer or getattr(self._layer_manager, "privacy", None)
                    if _privacy and hasattr(_privacy, "create_privacy_context"):
                        self._privacy_context_id = await _privacy.create_privacy_context()
                        log.info("layers privacy_context created: %s", self._privacy_context_id)
                except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    log.warning("layers privacy_context init failed: %s", _e)
            except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.warning("layers LayerManager init failed: %s", _e)
        if ct_log_client is not None:
            self._ct_log_client = ct_log_client
        try:
            self.sprint_id = getattr(lifecycle, "sprint_id", "") or ""
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            self.sprint_id = ""
        from core.telemetry.context_state import set_current_sprint_id

        set_current_sprint_id(self.sprint_id)
        self._lifecycle = lifecycle
        self._lc_adapter = adapter
        self._query = query
        if policy_manager is not None:
            self.inject_policy_manager(policy_manager)
        self._wall_clock_start = _time.monotonic()
        _effective_windup = self._config.effective_windup_lead_s
        _final_windup = self._config.final_windup_lead_s
        _active_window_budget = max(30.0, self._config.sprint_duration_s - _effective_windup)
        logger.info(
            "[WINDUP] effective_windup_lead_s=%.1fs final_windup_lead_s=%.1fs active_window_budget=%.1fs sprint_duration=%.1fs",
            _effective_windup,
            _final_windup,
            _active_window_budget,
            self._config.sprint_duration_s,
        )
        self._hard_deadline_monotonic = self._wall_clock_start + _active_window_budget
        self._result.hard_deadline_monotonic = self._hard_deadline_monotonic
        self._result.arrow_batch_hard_cap = self._ARROW_BATCH_HARD_CAP
        self._duckdb_store = duckdb_store
        self._duckdb_can_ingest = duckdb_store is not None and hasattr(duckdb_store, "async_ingest_findings_batch")
        _dedup_t0 = _time.monotonic()
        self._dedup_loading_task: asyncio.Task | None = safe_create_task(
            self._load_dedup(), name="sprint:dedup_lazy_load"
        )
        try:
            if self._injected_ioc_graph is not None:
                self.inject_ioc_graph(self._injected_ioc_graph)
            else:
                from hledac.universal.knowledge.graph_service import _get_graph

                self.inject_ioc_graph(_get_graph())
        except Exception as _e:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
            logger.debug(f"[OODA] graph injection failed: {_e}")
        self._runner = SprintLifecycleRunner(lifecycle, adapter)
        self._runner.setup()
        self._reset_result()

        async def _broadcast_start() -> None:
            try:
                if self._communication_layer is not None:
                    _broadcast = getattr(self._communication_layer, "broadcast_message", None)
                    if _broadcast is not None:
                        _payload = {"event": "sprint_start", "sprint_id": self.sprint_id, "query": self._query}
                        if asyncio.iscoroutine(_broadcast(_payload)):
                            await _broadcast(_payload)
            except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                pass

        _init_tasks: list[Awaitable] = [self._init_metrics_registry(), _broadcast_start()]
        if self._enrichment_services:
            _init_tasks.append(self._enrichment_services.init())
        _results: list = await safe_gather_ok(*_init_tasks, label="sprint_scheduler:_prelude_init_blocking")
        _dedup_elapsed = _time.monotonic() - _dedup_t0
        self._result.dedup_preload_elapsed_s = _dedup_elapsed

        async def _init_rel_discovery() -> None:
            try:
                from hledac.universal.intelligence.relationship_discovery import RelationshipDiscoveryEngine
                from hledac.universal.paths import LMDB_ROOT

                self._rel_discovery_engine = RelationshipDiscoveryEngine(use_sparse=True, max_memory_mb=512)
                _rel_graph_path = LMDB_ROOT / "rel_discovery_graph.pkl"
                if _rel_graph_path.exists():
                    self._rel_discovery_engine.load_graph(_rel_graph_path)
                    log.debug(f"[RelDiscovery] Loaded graph: {_rel_graph_path}")
                from hledac.universal.knowledge.graph_service import _DEFAULT_GRAPH_SERVICE

                def _cb(src: str, dst: str, rel_type: str, weight: float) -> None:
                    try:
                        _DEFAULT_GRAPH_SERVICE.upsert_relation(
                            src, dst, rel_type, weight=weight, evidence="rel_discovery_callback"
                        )
                    except Exception as _cb_e:  # noqa: BLE001 — best-effort; callback handler; non-critical
                        log.debug(f"[RelDiscovery] callback upsert failed: {_cb_e}")

                _DEFAULT_GRAPH_SERVICE.register_relationship_callback(_cb)
                log.debug("[RelDiscovery] Registered callback on GraphService")
            except Exception as _e:  # noqa: BLE001 — best-effort; callback handler; non-critical
                log.warning(f"[RelDiscovery] init failed: {_e}")
                self._rel_discovery_engine = None

        safe_create_task(_init_rel_discovery(), name="rel_discovery_init")
        global _gc_sprint_callback_handle
        if _gc_sprint_callback_handle is not None:
            try:
                _gc_sprint_callback_handle.cleanup()
            except Exception:  # noqa: BLE001 — best-effort; callback handler; non-critical
                pass
        _gc_sprint_stats.clear()
        _gc_sprint_callback_handle = _SprintCleanupHandle(self, _gc_sprint_sentinel)
        _trace_snap_before: Any = None
        _trace_enabled = bool(ENV.get_str("HLEDAC_TRACEMALLOC"))
        if _trace_enabled:
            try:
                import tracemalloc

                tracemalloc.start(16)
                _trace_snap_before = tracemalloc.take_snapshot()
            except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.warning(f"[E2] tracemalloc start failed: {_e}")
                _trace_enabled = False
        self._tick_metrics_on_cycle_end()

        def _init_evidence_chain() -> None:
            try:
                from hledac.universal.knowledge.evidence_chain import EvidenceChainBuilder, set_global_builder

                set_global_builder(EvidenceChainBuilder())
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass

        safe_create_task(asyncio.to_thread(_init_evidence_chain), name="evidence_chain_init")
        self._runner.tick(now_monotonic)
        self._runner.ensure_active(now_monotonic)
        if _dedup_elapsed > 1.0 and (not self._result.pre_loop_blocker_reason):
            self._result.pre_loop_blocker_reason = "dedup_preload"
        try:
            self._timer.phase("memory_preflight")
        finally:
            self._timer.phase("memory_preflight_end")
        self._barrier_import_done.set()
        return (_dedup_elapsed, _trace_enabled, _trace_snap_before)

    async def _init_background_transports(self) -> None:
        """Phase 3: Initialize background transports - memory pressure, DHT, I2P, Nym, Tor."""
        _t = safe_create_task(self._memory_pressure_loop(), name="sprint:memory_pressure_loop")
        self._bg_tasks.add(_t)
        _t.add_done_callback(self._bg_tasks.discard)
        if ENV.get_bool("HLEDAC_ENABLE_DHT"):
            _dht_t = safe_create_task(self._init_dht_node_background(), name="sprint:dht_init")
            self._bg_tasks.add(_dht_t)
            _dht_t.add_done_callback(self._bg_tasks.discard)
        if ENV.get_bool("HLEDAC_ENABLE_I2P"):
            _i2p_t = safe_create_task(self._init_i2p_background(), name="sprint:i2p_init")
            self._bg_tasks.add(_i2p_t)
            _i2p_t.add_done_callback(self._bg_tasks.discard)
        if ENV.get_bool("HLEDAC_ENABLE_NYM"):
            _nym_t = safe_create_task(self._init_nym_background(), name="sprint:nym_init")
            self._bg_tasks.add(_nym_t)
            _nym_t.add_done_callback(self._bg_tasks.discard)
        _tor_gate = ENV.get_bool("HLEDAC_ENABLE_TOR")
        _tor_proxy = bool(ENV.get_str("HLEDAC_TOR_PROXY"))
        if _tor_gate or _tor_proxy:
            _tor_t = safe_create_task(self._init_tor_background(), name="sprint:tor_init")
            self._bg_tasks.add(_tor_t)
            _tor_t.add_done_callback(self._bg_tasks.discard)

    async def _teardown_sprint(self, _trace_enabled: bool, _trace_snap_before: Any) -> None:
        """Phase D: Teardown - cleanup resources at sprint end (tracemalloc, GC, privacy context)."""
        try:
            if self._governor is not None:
                _final_dec = await self._governor.evaluate()
                self._result.governor_uma_state = getattr(_final_dec, "uma_state", "")
                self._result.governor_system_used_gib = getattr(_final_dec, "system_used_gib", 0.0)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        if _trace_enabled:
            try:
                import tracemalloc

                _trace_snap_after = tracemalloc.take_snapshot()
                _trace_diff = _trace_snap_after.compare_to(_trace_snap_before, "lineno")
                log.info("[E2] tracemalloc top 10 allocations:")
                for _stat in _trace_diff[:10]:
                    log.info("  %s", _stat)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
            try:
                tracemalloc.stop()
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        global _gc_sprint_callback_handle, _gc_callback_registered
        if _gc_sprint_callback_handle is not None:
            try:
                _gc_sprint_callback_handle.cleanup()
            except Exception:  # noqa: BLE001 — best-effort; callback handler; non-critical
                pass
            _gc_sprint_callback_handle = None
        _gc_callback_registered = False
        try:
            _privacy = self._privacy_layer or getattr(self._layer_manager, "privacy", None)
            if _privacy and hasattr(_privacy, "close_privacy_context"):
                await _privacy.close_privacy_context(self._privacy_context_id)
                log.debug("privacy_context closed: %s", self._privacy_context_id)
        except Exception as _e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
            log.debug("privacy_context close failed: %s", _e)
        if self._prefetch_pipeline is not None:
            try:
                await self._prefetch_pipeline.stop()
                log.debug("[P3-3] Prefetch pipeline stopped")
            except Exception as _e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                log.debug("[P3-3] Prefetch pipeline stop failed: %s", _e)

    @_otel_instrumented("sprint.scheduler.run", component="scheduler")
    async def run(
        self,
        lifecycle: Any,
        sources: Sequence[str],
        now_monotonic: float | None = None,
        query: str = "",
        duckdb_store: Any = None,
        ct_log_client: Any = None,
        policy_manager: Any = None,
        progress_callback: Any | None = None,
    ) -> SprintSchedulerResult:
        """

        Recursion guard + thin wrapper around _run_internal().

        Tracks _sprint_depth to prevent infinite recursion via self-calls
        (DoH prelude / pre-windup barrier / tiered feed sub-sprint). MAX depth = 3.
        """
        _token = _sprint_run_ctx.set(SprintRunContext())
        self._health_cache = None
        self._sprint_depth += 1
        if self._sprint_depth > 3:
            import logging as _logging

            _logging.getLogger(__name__).critical(
                "RECURSION GUARD: SprintScheduler.run depth=%d > 3 — aborting to prevent infinite recursion",
                self._sprint_depth,
            )
            raise RecursionError(f"SprintScheduler recursion depth exceeded: {self._sprint_depth}")
        try:
            self._duckdb_writer_shutdown = asyncio.Event()
            self._duckdb_writer_task = safe_create_task(self._duckdb_background_writer())
            self._memory_pressure_shutdown = asyncio.Event()
            return await self._run_internal(
                lifecycle,
                sources,
                now_monotonic=now_monotonic,
                query=query,
                duckdb_store=duckdb_store,
                ct_log_client=ct_log_client,
                policy_manager=policy_manager,
                progress_callback=progress_callback,
            )
        finally:
            _sprint_run_ctx.reset(_token)
            self._sprint_depth -= 1

    async def _run_internal(
        self,
        lifecycle: Any,
        sources: Sequence[str],
        now_monotonic: float | None = None,
        query: str = "",
        duckdb_store: Any = None,
        ct_log_client: Any = None,
        policy_manager: Any = None,
        progress_callback: Any | None = None,
    ) -> SprintSchedulerResult:
        """

        Run the sprint to completion.



        Args:

            lifecycle: SprintLifecycleManager instance (owned by caller)

            sources: ordered list of feed URLs to process

            now_monotonic: optional fake clock for testing



        Returns:

            SprintSchedulerResult with final statistics

        """
        adapter: _LifecycleAdapter | None = None
        from hledac.universal.brain.mlx_worker_thread import MLXWorkerThread

        _mlx_prewarm_worker = MLXWorkerThread(name="mlx-prewarm")
        _mlx_prewarm_worker.start()

        async def _prewarm_all_models() -> None:
            """Load all MLX models concurrently in shared event loop.

            Uses asyncio.gather for true parallelism - loop stays in one thread,
            but all three model loads run concurrently via run_in_executor.

            F320: Skip entirely if prewarm_daemon already loaded models at startup.
            """
            from hledac.universal.runtime.prewarm_daemon import is_prewarm_done

            if is_prewarm_done():
                log.debug("[_prewarm_all_models] skipped — prewarm_daemon already loaded")
                return

            async def _prewarm_hermes() -> None:
                try:
                    await self._prewarm_hermes_for_sprint()
                except Exception as _e:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
                    self._hermes_prewarm_exception = _e
                    self._result.hermes_load_reason = f"prewarm_error:{type(_e).__name__}"
                    self._result.hermes_model_loaded = False

            async def _prewarm_modernbert() -> None:
                try:
                    from hledac.universal.brain.modernbert_engine import ModernBertEngine

                    engine = ModernBertEngine()
                    await engine.load()
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass

            async def _prewarm_mlx_embed() -> None:
                """F275-5: Persistent prewarm with marker-based cache detection."""
                try:
                    from hledac.universal.compat.core_mlx_embeddings import get_embedding_manager

                    mgr = get_embedding_manager()
                    if mgr is not None and (not mgr._is_loaded):
                        await asyncio.to_thread(mgr.prewarm)
                except Exception:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
                    pass

            _cf_future = _mlx_prewarm_worker.prewarm_all(
                [_prewarm_hermes(), _prewarm_modernbert(), _prewarm_mlx_embed()], timeout_s=120.0
            )
            self._hermes_prewarm_task = _cf_future

        try:

            def _on_phase_transition(old: str, new: str) -> None:
                from transport.transport_supervisor import get_transport_supervisor

                sup = get_transport_supervisor()
                safe_create_task(sup.on_phase_boundary(old, new))

            adapter = _LifecycleAdapter(lifecycle, phase_transition_callback=_on_phase_transition)
        except Exception as _adapter_exc:  # noqa: BLE001 — best-effort; callback handler; non-critical
            logger.debug(f"[LIFECYCLE] adapter init failed: {_adapter_exc}")
        if self._prefetch_pipeline is not None:
            try:
                safe_create_task(asyncio.to_thread(self._prefetch_pipeline.start), name="prefetch_pipeline_start")
                logger.debug("[P3-3] Prefetch pipeline start triggered (fire-and-forget)")
            except Exception as _e:  # noqa: BLE001 — best-effort; prefetch/oracle failure; non-critical
                logger.debug("[P3-3] Prefetch pipeline start failed: %s", _e)
        self._query = query
        self._run_started_at: float = _time.monotonic()
        try:
            self._memory_delta_tracker.sprint_start()
        except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            pass
        _run_error: Exception | None = None
        _run_error_class: str = "unknown"
        try:
            _t0_prelude = _time.monotonic()
            _t_phase1 = _time.monotonic()
            _dedup_elapsed, _trace_enabled, _trace_snap_before = await self._initialize_sprint_run(
                adapter, lifecycle, ct_log_client, policy_manager, duckdb_store, now_monotonic, query=query
            )
            _t_phase2 = _time.monotonic()
            if ENV.get_bool("HLEDAC_ENABLE_DSPY") and query:
                try:
                    from hledac.universal.brain.dspy_service import expand_query

                    expanded = await expand_query(query)
                    if expanded and expanded:
                        _expanded_capped = expanded[:3]
                        log.debug(
                            "[HERMES3_WIRING] DSPy expanded %d queries for '%s...'", len(_expanded_capped), query[:30]
                        )
                        self._result.next_seeds_query_suggestions = tuple(_expanded_capped)
                except Exception as _exc:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                    log.debug("[HERMES3_WIRING] DSPy expand_query failed: %s", _exc)
            _t_phase3 = _time.monotonic()
            _DEFAULT_SOURCE_TYPES = ["cisa_kev", "threatfox_ioc", "urlhaus_recent", "feodo_ip", "openphish_feed"]
            _graph_stats: dict[str, int] = {"nodes": 0, "edges": 0}
            ordered_sources = self.prioritize_sources(list(sources) if sources else _DEFAULT_SOURCE_TYPES, _graph_stats)
            _t_phase4 = _time.monotonic()
            _pre_loop_elapsed = _time.monotonic() - self._wall_clock_start
            self._result.pre_loop_elapsed_s = _pre_loop_elapsed
            self._result.entered_active_at_monotonic = _pre_loop_elapsed
            _active_window_budget = max(
                30.0, self._config.sprint_duration_s - self._config.effective_windup_lead_s - _pre_loop_elapsed
            )
            self._hard_deadline_monotonic = self._wall_clock_start + _active_window_budget
            self._result.hard_deadline_monotonic = self._hard_deadline_monotonic
            _pre_loop_cost = _pre_loop_elapsed
            _adapter = self._lc_adapter
            if _adapter is not None:
                _adapter.set_pre_loop_cost_s(_pre_loop_cost)
            elif hasattr(self._lifecycle, "pre_loop_cost_s"):
                self._lifecycle.pre_loop_cost_s = _pre_loop_cost
            _effective_windup = self._config.effective_windup_lead_s
            if _adapter is not None:
                _adapter.set_windup_lead_s(_effective_windup)
            elif hasattr(self._lifecycle, "windup_lead_s"):
                self._lifecycle.windup_lead_s = _effective_windup
            if _active_window_budget > 0 and (not self._check_hard_deadline()):
                log.warning(
                    f"[PRE-LOOP DEADLINE] Hard deadline exceeded before first cycle. pre_loop_elapsed={_pre_loop_elapsed:.1f}s, active_window_budget={_active_window_budget:.1f}s. Skipping to windup."
                )
                await self._ensure_nonfeed_predispatch_before_finalization(query, "pre_loop_deadline_exceeded")
                self._capture_timing_fields()
                await self._finalize_result_truth(
                    "pre_loop_deadline_exceeded",
                    "hard deadline exceeded before first cycle due to slow pre-loop init",
                    "GATHER",
                    query,
                )
                lifecycle.request_windup()
                return self._result
            _t_plan_start = _time.monotonic()
            try:
                self._timer.phase("runtime_pivot_seed_extraction_start")

                async def _get_governor_uma() -> tuple[str, bool]:
                    _gov_dec: Any = None
                    if self._governor is not None:
                        try:
                            _gov_dec = await self._governor.evaluate()
                            if _gov_dec is not None:
                                await self._governor.apply_decision(_gov_dec)
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
                    if _gov_dec is not None:
                        return (_gov_dec.uma_state, getattr(_gov_dec, "swap_detected", False))
                    from hledac.universal.core.resource_governor import sample_uma_status

                    uma = sample_uma_status()
                    return (uma.state if uma.state else "ok", uma.swap_detected)

                async def _load_next_seeds() -> tuple[list, Any, str]:
                    if not self._config.predecessor_sprint_id:
                        return ([], None, "")
                    try:
                        from hledac.universal.runtime.next_seeds_consumption import consume_next_sprint_seeds

                        iocs, diags, suggestions, skip = consume_next_sprint_seeds(self._config.predecessor_sprint_id)
                        return (iocs, diags, skip)
                    except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        log.debug("next_sprint_seeds consume failed: %s", _e)
                        return ([], None, "consume_failed")

                _gov_task = safe_create_task(_get_governor_uma())
                _seeds_task = safe_create_task(_load_next_seeds())
                _uma_state, _swap_detected = await _gov_task
                self._result.governor_uma_state = _uma_state
                self._result.governor_io_only = _swap_detected
                _next_seeds_ioc_seeds, _next_seeds_diagnostics, _next_seeds_skip_reason = await _seeds_task
                _t_plan_parallel_done = _time.monotonic()
                self._result.next_seeds_provider_yield = (
                    bool(getattr(_next_seeds_diagnostics, "provider_yield_active", False))
                    if _next_seeds_diagnostics
                    else False
                )
                self._result.next_seeds_pivot_deepening = (
                    bool(getattr(_next_seeds_diagnostics, "pivot_deepening_active", False))
                    if _next_seeds_diagnostics
                    else False
                )
                self._result.next_seeds_query_suggestions = (
                    getattr(_next_seeds_diagnostics, "query_suggestions", ()) if _next_seeds_diagnostics else ()
                )
                self._result.next_seeds_consumed_count = len(_next_seeds_ioc_seeds)
                self._result.next_seeds_seed_source = (
                    f"predecessor:{self._config.predecessor_sprint_id}"
                    if self._config.predecessor_sprint_id
                    else "none"
                )
                self._timer.phase("acquisition_plan_build_start")
                _synthetic_domains: list[str] = []
                _plan_kwargs = {
                    "query": query,
                    "duration_s": self._config.sprint_duration_s,
                    "aggressive_mode": self._config.aggressive_mode,
                    "uma_state": _uma_state,
                    "swap_detected": _swap_detected,
                    "accepted_findings_so_far": self._result.accepted_findings,
                    "branch_timeout_count": self._result.branch_timeout_count,
                    "acquisition_profile": self._config.acquisition_profile or "",
                    "source_quality_weights": self._policy_manager.get_src_quality_weights()
                    if self._policy_manager is not None and self._policy_manager.enabled
                    else None,
                    "rl_lane_combo": self._result.rl_lane_combo if self._result.rl_lane_combo else None,
                    "synthetic_domains": _synthetic_domains,
                }
                from hledac.universal.runtime.acquisition_strategy import build_acquisition_plan

                self._acquisition_plan = await asyncio.to_thread(build_acquisition_plan, **_plan_kwargs)
                self._timer.phase("acquisition_plan_build_end")
                _t_plan_done = _time.monotonic()
                from hledac.universal.runtime.acquisition_strategy import is_lane_enabled

                self._result.ct_planned = is_lane_enabled(self._acquisition_plan, "CT")
                self._result.doh_planned = is_lane_enabled(self._acquisition_plan, "DOH")
                self._result.nonfeed_plan_debug = getattr(self._acquisition_plan, "nonfeed_plan_debug", None)
                if self._result.nonfeed_plan_debug is not None:
                    try:
                        _nd = self._result.nonfeed_plan_debug
                        _graph_stats = self._get_pivot_graph_stats_for_planning()
                        _pivot_candidates = generate_pivot_candidates_from_query(
                            query, mission_intent=_nd.mission_intent, graph_stats=_graph_stats
                        )
                        _nd.pivot_candidates_count = len(_pivot_candidates)
                        _nd.pivot_candidate_types = tuple({p.pivot_type for p in _pivot_candidates})
                        _nd.pivot_scheduled_lanes = ()
                        _nd.pivot_skip_reason = None
                        _nd.pivot_errors = ()
                        if _graph_stats and _graph_stats.get("nodes", 0) > 0:
                            self._result.pivot_graph_stats_used = True
                            self._result.pivot_graph_stats_keys = tuple(_graph_stats.keys())
                            self._result.graph_aware_pivot_count = len(_pivot_candidates)
                    except Exception:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
                        pass
                if self._result.nonfeed_plan_debug is not None:
                    _nd = self._result.nonfeed_plan_debug
                    self._result.nonfeed_priority_enabled = getattr(_nd, "nonfeed_priority_enabled", False)
                    self._result.nonfeed_profile_expected_lanes = tuple(
                        (canonical_lane_name(x) for x in getattr(_nd, "nonfeed_profile_expected_lanes", ()) or ())
                    )
                    self._result.nonfeed_expected_lanes = tuple(
                        (canonical_lane_name(x) for x in getattr(_nd, "scheduled_nonfeed_lanes", ()) or ())
                    )
                    self._result.nonfeed_expected_lanes_source = "build_acquisition_plan.nonfeed_plan_debug"
            except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                _t_plan_start = _t_phase4
                _t_plan_parallel_done = _t_phase4
                _t_plan_done = _time.monotonic()
                self._acquisition_plan = None
                self._result.acquisition_plan_build_failed = True
                self._result.acquisition_plan_build_error_type = type(_exc).__name__
                self._result.acquisition_plan_build_error = str(_exc)[:500]
            # F351-FIX: ensure_connected must complete BEFORE parallel gather starts.
            # Fire-and-forget (safe_create_task) creates a race: _run_one_cycle may call
            # _gate_then_ingest_and_accumulate which calls ensure_connected() again — if the
            # background init hasn't finished, _init_connection() could race with itself.
            # Fix: call ensure_connected synchronously here so it completes before the
            # prelude_task + first_cycle_task are both scheduled via parallel().
            try:
                _ds = getattr(self, "_duckdb_store", None)
                if _ds is not None and hasattr(_ds, "ensure_connected"):
                    await asyncio.to_thread(_ds.ensure_connected)
            except Exception as _e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                log.debug("[P4.3] DuckDB pre-warm failed: %s", _e)
            _barrier = getattr(self, "_barrier_import_done", None)
            if _barrier is not None:
                try:
                    await safe_wait_for(_barrier.wait(), timeout=90.0, label="_barrier")
                except TimeoutError:
                    log.warning("[Sprint0] _barrier_import_done timed out after 90s — continuing anyway")
                except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    log.debug("[Sprint0] barrier wait failed: %s", _e)
            _t_hermes_wait_start = _time.monotonic()
            _hermes_task = getattr(self, "_hermes_prewarm_task", None)
            if _hermes_task is not None:
                try:
                    if isinstance(_hermes_task, asyncio.Task):
                        await _hermes_task
                    else:
                        _hermes_task.result(timeout=0)
                except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    log.debug("[P0-1] Hermes prewarm await failed: %s", _e)
            _t_hermes_wait_done = _time.monotonic()
            try:
                self._timer.phase("prelude_start")
                from hledac.universal.utils.async_helpers import safe_create_task

                prelude_task = safe_create_task(
                    self._run_mandatory_acquisition_prelude(self._result, query, duckdb_store, self._ct_log_client),
                    name="sprint:prelude",
                    eager_start=True,
                )
            finally:
                self._timer.phase("prelude_end")
            _t_work_items = _time.monotonic()
            self._build_work_items(ordered_sources)
            first_cycle_task = safe_create_task(
                self._run_one_cycle(
                    lifecycle, ordered_sources, now_monotonic=None, query=query, duckdb_store=duckdb_store
                ),
                name="sprint:first_cycle",
                eager_start=True,
            )
            _t_branch_start = _time.monotonic()
            _parallel_result = await parallel(
                [prelude_task, first_cycle_task], concurrency=2, ctx="prelude_first_cycle"
            )
            _ok_results, _gathered_errors = _parallel_result.ok, list(_parallel_result.errors)
            _prelude_exc = _ok_results[0] if len(_ok_results) > 0 else None
            _cycle_exc = _ok_results[1] if len(_ok_results) > 1 else None
            _t_gather_done = _time.monotonic()
            _branch_dur = _t_gather_done - _t_branch_start
            _phase1_dur = _t_phase2 - _t_phase1
            _phase3_dur = _t_phase3 - _t_phase2
            _phase4_dur = _t_phase4 - _t_phase3
            _plan_parallel_dur = _t_plan_parallel_done - _t_plan_start
            _plan_build_dur = _t_plan_done - _t_plan_parallel_done
            _plan_total_dur = _t_plan_done - _t_plan_start
            _hermes_wait_dur = _t_hermes_wait_done - _t_hermes_wait_start
            _work_items_dur = _t_work_items - _t_hermes_wait_done
            _prelude_dispatch_dur = _t_gather_done - _t_work_items
            _pre_loop_total = _time.monotonic() - _t0_prelude
            logger.info(
                "[P4.3][pre-loop-cost] init=%.1fs dspy=%.1fs src_prioritize=%.1fs pre_loop_capture=%.1fs plan_parallel=%.1fs plan_build=%.1fs plan_total=%.1fs hermes_wait=%.1fs work_items=%.1fs branch=%.1fs prelude_dispatch=%.1fs total=%.1fs",
                _phase1_dur,
                _phase3_dur,
                _phase4_dur,
                _t_phase4 - _t_phase3,
                _plan_parallel_dur,
                _plan_build_dur,
                _plan_total_dur,
                _hermes_wait_dur,
                _work_items_dur,
                _branch_dur,
                _prelude_dispatch_dur,
                _pre_loop_total,
            )
            if isinstance(_prelude_exc, BaseException) and (not isinstance(_prelude_exc, asyncio.CancelledError)):
                log.warning("[sprint] prelude raised: %s: %s", type(_prelude_exc).__name__, _prelude_exc)
            if isinstance(_cycle_exc, BaseException) and (not isinstance(_cycle_exc, asyncio.CancelledError)):
                log.warning("[sprint] first cycle raised: %s: %s", type(_cycle_exc).__name__, _cycle_exc)
            self._result.cycles_completed += 1
            if self._hermes_engine is not None:
                self._hermes_engine._active_iteration_count = self._result.cycles_started
            if getattr(self._result, "acquisition_prelude_ran", False):
                await self._finalize_result_truth("prelude_complete", "acquisition prelude finished", "ACTIVE", query)
            if isinstance(_prelude_exc, asyncio.CancelledError):
                raise _prelude_exc
            if isinstance(_cycle_exc, asyncio.CancelledError):
                raise _cycle_exc
            if not self._check_hard_deadline():
                await self._ensure_nonfeed_predispatch_before_finalization(query, "hard_deadline_exceeded")
                self._capture_timing_fields()
                await self._finalize_result_truth(
                    "hard_deadline_exceeded", "hard deadline exceeded after first_cycle_gather", "GATHER", query
                )
                return self._result
            try:
                while not self._runner.is_terminal():
                    now_monotonic = _time.monotonic()
                    if not self._check_hard_deadline():
                        await self._ensure_nonfeed_predispatch_before_finalization(query, "hard_deadline_exceeded")
                        self._capture_timing_fields()
                        await self._finalize_result_truth(
                            "hard_deadline_exceeded",
                            f"hard deadline exceeded at cycle {self._result.cycles_started}",
                            "GATHER",
                            query,
                        )
                        lifecycle.request_windup()
                        break
                    if self._stop_requested:
                        if await self._ensure_mandatory_nonfeed_before_return(query, duckdb_store, "stop_requested"):
                            await self._ensure_nonfeed_predispatch_before_finalization(query, "stop_requested_break")
                            self._capture_timing_fields()
                            await self._finalize_result_truth(
                                "stop_requested_break", "stop_requested guard passed", "GATHER", query
                            )
                            lifecycle.request_windup()
                            break
                        continue
                    if self._runner.abort_requested:
                        self._result.aborted = True
                        self._result.abort_reason = self._runner.abort_reason or "lifecycle_abort"
                        await self._maybe_export_partial(lifecycle)
                        await self._ensure_mandatory_nonfeed_before_return(query, duckdb_store, "lifecycle_abort")
                        await self._ensure_nonfeed_predispatch_before_finalization(query, "lifecycle_abort_break")
                        self._capture_timing_fields()
                        await self._finalize_result_truth(
                            "lifecycle_abort_break", "abort_requested from lifecycle", "GATHER", query
                        )
                        lifecycle.request_windup()
                        break
                    self._runner.tick(now_monotonic)
                    await self._maybe_dispatch_nonfeed_probe_lanes(query, duckdb_store)
                    self._result.windup_guard_call_count += 1
                    _barrier_result = await self._ensure_pre_windup_lane_terminal_states(
                        query, self._acquisition_plan, "ok"
                    )
                    _barrier_satisfied = getattr(_barrier_result, "satisfied", False)
                    _barrier_required = getattr(_barrier_result, "required_lanes", ())
                    _barrier_delayed = self._prewindup_barrier_delayed
                    if _barrier_required and (not _barrier_satisfied):
                        _barrier_retry_count = getattr(self, "_barrier_retry_count", 0) + 1
                        _barrier_max_retries = 3
                        _barrier_hard_timeout_s = 30.0
                        self._barrier_retry_count = _barrier_retry_count
                        if _barrier_retry_count > _barrier_max_retries:
                            log.warning(
                                "[F223-D] prewindup barrier timeout after %d retries -- forcing windup",
                                _barrier_retry_count,
                            )
                            _barrier_satisfied = True
                        elif now_monotonic - self._wall_clock_start > _barrier_hard_timeout_s:
                            log.warning(
                                "[F223-D] prewindup barrier hard timeout %.1fs -- forcing windup",
                                _barrier_hard_timeout_s,
                            )
                            _barrier_satisfied = True
                        elif not _barrier_delayed:
                            self._prewindup_barrier_delayed = True
                            self._result.prewindup_barrier_delayed_cycle = True
                            log.debug(
                                "[F207S-B] Prewindup barrier not satisfied (required=%s) -- delaying cycle once",
                                _barrier_required,
                            )
                            continue
                    _sprint_elapsed = now_monotonic - self._wall_clock_start
                    _remaining_s = max(0.0, self._config.sprint_duration_s - _sprint_elapsed)
                    await self._drain_pending_pattern_extractions(_remaining_s)
                    self._maybe_call_pressure_relief()
                    _guard_result = self._runner.windup_guard(
                        now_monotonic,
                        pre_windup_barrier=lambda: self._check_prewindup_barrier_sync(query, duckdb_store),
                    )
                    _obs = self._runner.last_guard_observation
                    if _obs:
                        if _obs.get("callback_supplied"):
                            self._result.windup_guard_callback_supplied_count += 1
                        self._result.windup_guard_callback_executed_count += 1 if _obs.get("callback_executed") else 0
                        self._result.windup_guard_last_reason = _obs.get("reason", "")
                        self._result.windup_guard_last_phase = _obs.get("phase", "")
                        self._result.windup_guard_last_allowed = _obs.get("allowed")
                        self._result.windup_guard_last_callback_not_executed_reason = _obs.get(
                            "callback_not_executed_reason", ""
                        )
                    if _guard_result:
                        if not self._nonfeed_predispatch_done:
                            log.debug("[F207M-A] Windup signalled but pre-dispatch not done -- yielding")
                            await self._maybe_dispatch_nonfeed_probe_lanes(query, duckdb_store)
                        await self._flush_dedup()
                        safe_create_task(
                            self._run_ioc_cooccurrence_sidecar(query, duckdb_store), name="sprint:ioc_cooccurrence"
                        )
                        self._synth_windup_task = safe_create_task(
                            self._run_synthesis_sidecar(query, duckdb_store, lifecycle), name="sprint:synthesis_windup"
                        )
                        await self._run_epistemic_gap_advisory(query, duckdb_store)
                        if self._enrichment_services:
                            await self._enrichment_services.flush()
                        self.evaluate_advisory_gate()
                        await self._maybe_export_partial(lifecycle)
                        if await self._ensure_mandatory_nonfeed_before_return(query, duckdb_store, "windup_barrier"):
                            await self._ensure_nonfeed_predispatch_before_finalization(query, "windup_barrier_passed")
                            self._capture_timing_fields()
                            await self._finalize_result_truth(
                                "windup_barrier_passed", "pre-windup barrier satisfied, entered windup", "WINDUP", query
                            )
                            break
                        await self._ensure_mandatory_nonfeed_before_return(query, duckdb_store, "windup_barrier_forced")
                        await self._ensure_nonfeed_predispatch_before_finalization(query, "windup_barrier_break")
                        self._capture_timing_fields()
                        await self._finalize_result_truth(
                            "windup_barrier_break",
                            "pre-windup barrier unsatisfied, forced terminalization",
                            "WINDUP",
                            query,
                        )
                        break
                    current_phase_str = self._runner.current_phase
                    if current_phase_str == "ACTIVE":
                        ordered_sources = self.prioritize_sources(ordered_sources, _graph_stats)
                    if not hasattr(self, "_cycle_time_ema"):
                        self._cycle_time_ema = 1.0
                        self._last_cycle_start: float | None = None
                        self._effective_max_cycles = self._config.max_cycles
                    if self._last_cycle_start is not None:
                        _elapsed = _time.monotonic() - self._last_cycle_start
                        _elapsed = max(0.1, min(10.0, _elapsed))
                        self._cycle_time_ema = 0.7 * self._cycle_time_ema + 0.3 * _elapsed
                        _active = max(0.0, self._config.sprint_duration_s - self._config.final_windup_lead_s)
                        if _active > 0 and self._cycle_time_ema > 0:
                            self._effective_max_cycles = max(50, min(300, int(_active / self._cycle_time_ema)))
                    if self._result.cycles_started >= self._effective_max_cycles:
                        await self._ensure_mandatory_nonfeed_before_return(query, duckdb_store, "max_cycles")
                        await self._ensure_nonfeed_predispatch_before_finalization(query, "max_cycles_break")
                        self._capture_timing_fields()
                        await self._finalize_result_truth(
                            "max_cycles_break", "cycles >= max_cycles reached", "GATHER", query
                        )
                        break
                    if self._result.cycles_started == 0:
                        _elapsed = _time.monotonic() - _t0_prelude
                        logger.info(
                            "[prelude] completed in %.1fs (budget=%.0fs)",
                            _elapsed,
                            self._config.effective_windup_lead_s,
                        )
                    self._result.cycles_started += 1
                    if self._hermes_engine is not None:
                        self._hermes_engine._active_iteration_count = self._result.cycles_started
                    self._last_cycle_start = _time.monotonic()
                    if self._result.first_cycle_started_at_monotonic is None:
                        self._result.first_cycle_started_at_monotonic = _time.monotonic() - self._wall_clock_start
                        gap = self._result.first_cycle_started_at_monotonic - self._result.entered_active_at_monotonic
                        if gap > 30.0:
                            self._result.pre_active_starved = True
                            if not self._result.pre_loop_blocker_reason:
                                self._result.pre_loop_blocker_reason = "pre_loop_slow"
                        _pre_loop_cost = self._result.pre_loop_elapsed_s or 0.0
                        if _pre_loop_cost > 0:
                            _adapter = self._lc_adapter
                            if _adapter is not None:
                                _adapter.set_pre_loop_cost_s(_pre_loop_cost)
                                _adapter.set_first_cycle_ran()
                            elif hasattr(self._lifecycle, "pre_loop_cost_s"):
                                self._lifecycle.pre_loop_cost_s = _pre_loop_cost
                                if hasattr(self._lifecycle, "first_cycle_ran"):
                                    self._lifecycle.first_cycle_ran = True
                    elapsed_wall = _time.monotonic() - self._wall_clock_start
                    if elapsed_wall > self._config.sprint_duration_s + self._config.cycle_sleep_s:
                        log.warning(
                            f"[8BK] Duration budget exceeded: {elapsed_wall:.1f}s > {self._config.sprint_duration_s + self._config.cycle_sleep_s:.1f}s (grace={self._config.cycle_sleep_s:.1f}s). Forcing windup."
                        )
                        await self._ensure_mandatory_nonfeed_before_return(query, duckdb_store, "duration_budget")
                        await self._ensure_nonfeed_predispatch_before_finalization(query, "duration_budget_break")
                        self._capture_timing_fields()
                        await self._finalize_result_truth(
                            "duration_budget_break", "duration_budget exhausted", "GATHER", query
                        )
                        lifecycle.request_windup()
                        break
                    self._last_sources = list(ordered_sources)
                    _ema = getattr(self, "_cycle_time_ema", 0.0) or 0.0
                    _fixed = self._config.cycle_budget_s
                    if _ema > 0:
                        _adaptive = min(_fixed * 1.5, _ema * 3.0, 120.0)
                        _cycle_budget_s = max(30.0, _adaptive)
                    else:
                        _cycle_budget_s = _fixed
                    _elapsed_wall = _time.monotonic() - self._wall_clock_start
                    _remaining_active = max(0.0, self._config.sprint_duration_s - _elapsed_wall)
                    _min_active_window_s: float = 30.0
                    _min_empty_for_early_exit: int = 3
                    if (
                        _remaining_active < _min_active_window_s
                        and self._result.consecutive_empty_cycles >= _min_empty_for_early_exit
                    ):
                        log.warning(
                            "[P1-1] Early windup: remaining=%.1fs < %.0fs and %d empty cycles",
                            _remaining_active,
                            _min_active_window_s,
                            self._result.consecutive_empty_cycles,
                        )
                        try:
                            await check_zero_findings_alert(
                                elapsed_s=_elapsed_wall,
                                consecutive_empty_cycles=self._result.consecutive_empty_cycles,
                                total_findings=self._result.accepted_findings,
                            )
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
                        await self._ensure_nonfeed_predispatch_before_finalization(query, "early_windup_empty_cycles")
                        self._capture_timing_fields()
                        await self._finalize_result_truth(
                            "early_windup_empty_cycles",
                            f"early windup: {self._result.consecutive_empty_cycles} empty cycles, remaining={_remaining_active:.0f}s < {_min_active_window_s:.0f}s",
                            "GATHER",
                            query,
                        )
                        break
                    try:
                        async with asyncio.timeout(_cycle_budget_s):
                            cycle_ok = await self._run_one_cycle(
                                lifecycle, ordered_sources, now_monotonic, query, duckdb_store
                            )
                    except TimeoutError:
                        self._cycle_timeout_count += 1
                        log.warning(
                            "[F-A3] cycle exceeded %.1fs budget (count=%d) -- counting as empty",
                            _cycle_budget_s,
                            self._cycle_timeout_count,
                        )
                        self._result.consecutive_empty_cycles += 1
                        if self._result.consecutive_empty_cycles > self._result.max_consecutive_empty_cycles:
                            self._result.max_consecutive_empty_cycles = self._result.consecutive_empty_cycles
                        cycle_ok = True
                    _empty_cycle_limit = max(4, min(8, int(self._config.sprint_duration_s / 30.0)))
                    if self._result.consecutive_empty_cycles >= _empty_cycle_limit:
                        log.warning(
                            "[F228G] %d consecutive empty cycles >= limit %d -- forcing windup",
                            self._result.consecutive_empty_cycles,
                            _empty_cycle_limit,
                        )
                        await self._ensure_nonfeed_predispatch_before_finalization(query, "empty_cycle_break")
                        self._capture_timing_fields()
                        await self._finalize_result_truth(
                            "empty_cycle_break",
                            f"empty cycles {self._result.consecutive_empty_cycles} >= limit {_empty_cycle_limit}",
                            "GATHER",
                            query,
                        )
                        break
                    self._result.cycles_completed += 1
                    self._tick_metrics_on_cycle_end()
                    if progress_callback is not None:
                        elapsed_s = _time.monotonic() - self._wall_clock_start
                        try:
                            progress_callback(self._result, current_phase_str, elapsed_s)
                        except Exception:  # noqa: BLE001 — best-effort; callback handler; non-critical
                            pass
                    await self._maybe_export_partial(lifecycle)
                    if current_phase_str == "ACTIVE":
                        pivot_n = await self._drain_pivot_queue()
                        if pivot_n:
                            log.debug(f"Pivot queue drained: {pivot_n} tasks, stats={self._pivot_stats}")
                    if not cycle_ok:
                        if await self._ensure_mandatory_nonfeed_before_return(query, duckdb_store, "cycle_ok_false"):
                            await self._ensure_nonfeed_predispatch_before_finalization(query, "cycle_ok_false_break")
                            self._capture_timing_fields()
                            await self._finalize_result_truth(
                                "cycle_ok_false_break", "cycle returned False, guard passed", "GATHER", query
                            )
                            break
                        continue
                    if self._config.stop_on_first_accepted and self._result.accepted_findings > 0:
                        self._result.stop_requested = True
                        if await self._ensure_mandatory_nonfeed_before_return(
                            query, duckdb_store, "stop_on_first_accepted"
                        ):
                            await self._ensure_nonfeed_predispatch_before_finalization(
                                query, "stop_on_first_accepted_break"
                            )
                            self._capture_timing_fields()
                            await self._finalize_result_truth(
                                "stop_on_first_accepted_break", "first accepted finding, stop", "GATHER", query
                            )
                            break
                        continue
                    await self._runner.sleep_or_abort(
                        self._config.effective_cycle_sleep_s, progress_callback, self._result
                    )
                    _fd = getattr(self._result, "feed_domain_seeds", ()) or ()
                    if _fd and self._acquisition_plan is not None:
                        _plan_dict = {p.lane: p for p in self._acquisition_plan.plans}
                        _nonfeed_disabled = not any(
                            (_plan_dict.get(lane_name.lower()) is not None for lane_name in ("ct", "doh", "wayback"))
                        )
                        if _nonfeed_disabled:
                            try:
                                from hledac.universal.runtime.acquisition_strategy import build_acquisition_plan

                                _uma_state = "ok"
                                if self._governor is not None:
                                    try:
                                        _snap = await self._governor.evaluate()
                                        _uma_state = getattr(_snap, "uma_state", "ok")
                                    except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                        pass
                                self._acquisition_plan = build_acquisition_plan(
                                    query=query,
                                    duration_s=self._config.sprint_duration_s,
                                    aggressive_mode=self._config.aggressive_mode,
                                    uma_state=_uma_state,
                                    swap_detected=False,
                                    accepted_findings_so_far=self._result.accepted_findings,
                                    branch_timeout_count=self._result.branch_timeout_count,
                                    acquisition_profile=self._config.acquisition_profile or "",
                                    source_quality_weights=None,
                                    rl_lane_combo=None,
                                    feed_domain_seeds=_fd,
                                    synthetic_domains=(),
                                )
                                log.debug(
                                    "[P5] Mid-sprint re-plan: %d feed_domain_seeds enabled CT/DOH/WAYBACK lanes",
                                    len(_fd),
                                )
                            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                pass
                    if self._runner.post_sleep_gate(now_monotonic):
                        await self._maybe_export_partial(lifecycle)
                        _MIN_LANES_FOR_EARLY_WINDUP: int = 2
                        _lanes_with_evidence = len(self._result.entries_per_source)
                        if (
                            await self._ensure_mandatory_nonfeed_before_return(query, duckdb_store, "post_sleep_windup")
                            and _lanes_with_evidence >= _MIN_LANES_FOR_EARLY_WINDUP
                        ):
                            await self._ensure_nonfeed_predispatch_before_finalization(query, "post_sleep_windup_break")
                        _rescue_triggered = False
                        if (
                            self._result.accepted_findings >= 1000
                            and self._result.lane_ct_accepted_findings == 0
                            and (self._result.lane_wayback_accepted_findings == 0)
                            and (self._result.lane_pdns_accepted_findings == 0)
                            and (self._result.lane_blockchain_accepted_findings == 0)
                            and (self._result.lane_ipfs_accepted_findings == 0)
                            and (self._result.lane_doh_accepted_findings == 0)
                            and (self._result.requested_duration_s >= 180)
                        ):
                            _rescue_elapsed = await self._run_feed_dominance_nonfeed_rescue_window(query, duckdb_store)
                            if _rescue_elapsed is not None and _rescue_elapsed > 0:
                                _rescue_triggered = True
                                log.info(
                                    f"[F220D] Feed dominance rescue window completed in {_rescue_elapsed:.1f}s, nonfeed findings: ct={self._result.lane_ct_accepted_findings} public={self._result.public_accepted_findings}"
                                )
                            else:
                                log.debug("[F220D] Feed dominance rescue window returned no candidates")
                        self._capture_timing_fields()
                        if _rescue_triggered:
                            await self._finalize_result_truth(
                                "post_sleep_windup_break", "feed dominant nonfeed rescue attempted", "WINDUP", query
                            )
                            break
                        else:
                            await self._finalize_result_truth(
                                "post_sleep_windup_break", "post_sleep gate windup, guard passed", "WINDUP", query
                            )
                            break
                    now_mono = _time.monotonic()
                    if now_mono - self._last_speculative >= 15.0:
                        _t = safe_create_task(self._speculative_prefetch(n=3), name="sprint:speculative_prefetch")
                        self._bg_tasks.add(_t)
                        _t.add_done_callback(self._bg_tasks.discard)
                        self._last_speculative = now_mono
                    if now_mono - self._last_ooda >= self._ooda_interval:
                        _t = safe_create_task(self._run_ooda_cycle(self._pivot_ioc_graph), name="sprint:ooda_cycle")
                        self._bg_tasks.add(_t)
                        _t.add_done_callback(self._bg_tasks.discard)
                        self._last_ooda = now_mono
            except Exception as exc:  # noqa: BLE001 — best-effort; callback handler; non-critical
                self._runner.abort(f"scheduler_exception:{type(exc).__name__}")
                self._result.aborted = True
                self._result.abort_reason = f"{type(exc).__name__}"
                self._run_exit_path_override = "aborted_by_error"
            finally:
                global _gc_sprint_callback_handle, _gc_callback_registered
                if _gc_sprint_callback_handle is not None:
                    try:
                        _gc_sprint_callback_handle.cleanup()
                    except Exception:  # noqa: BLE001 — best-effort; callback handler; non-critical
                        pass
                    _gc_sprint_callback_handle = None
                    _gc_callback_registered = False
                    if _gc_sprint_stats:
                        log.debug(f"[E4] GC sprint stats: {len(_gc_sprint_stats)} collections")
                if _trace_snap_before is not None and _trace_enabled:
                    try:
                        import tracemalloc

                        tracemalloc.stop()
                        snap_after = tracemalloc.take_snapshot()
                        diff = snap_after.compare_to(_trace_snap_before, "lineno")
                        for stat in diff[:10]:
                            log.info(f"[E2] Alloc delta: {stat}")
                    except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        log.warning(f"[E2] tracemalloc compare failed: {_e}")
                    finally:
                        try:
                            del snap_after
                        except NameError:
                            pass
                        try:
                            del diff
                        except NameError:
                            pass
            self._maybe_call_pressure_relief()
            self._runner.teardown()
            try:
                from hledac.universal.intelligence.entity_signal_extractor import (
                    reset_extractor_stats,
                    shutdown_executor,
                )

                shutdown_executor()
                reset_extractor_stats()
            except Exception:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
                pass
            if ENV.get_bool("HLEDAC_ENABLE_NODRIVER"):
                try:
                    from fetching.public_fetcher import _teardown_browser_pool

                    await _teardown_browser_pool()
                    logger.debug("[winddown] browser pool torn down")
                except Exception as _e:  # noqa: BLE001 — best-effort; logging failure; non-critical
                    logger.debug("[winddown] browser pool teardown skipped: %s", _e)
            if self._config.export_enabled:
                try:
                    self._timer.phase("export_start")
                    await self._run_export(lifecycle)
                finally:
                    self._timer.phase("export_end")
            _synth_task = getattr(self, "_synth_windup_task", None)
            if _synth_task is not None:
                await _synth_task
                self._synth_windup_task = None
            _store = getattr(self, "_duckdb_store", None)
            if _store is not None:
                try:
                    await _store.async_vacuum_if_needed(threshold_bytes=2 * 1024**3)
                except Exception as _e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                    logger.debug("[winddown] duckdb vacuum skipped: %s", _e)
            self._result.final_phase = self._runner.current_phase
            _layout = getattr(self._result, "_int_counter_layout", None)
            if self._evidence_log is not None and _layout is not None:
                try:
                    from hledac_rust_extensions import chain_hash_snapshot

                    _snap = _layout.snapshot()
                    _prev = self._prev_chain_hash or ""
                    _event_id = f"sprint_end_{getattr(self, '_sprint_id', 'unknown')}"
                    _blake3_hex, _sha256_hex = chain_hash_snapshot(_snap, _prev, _event_id)
                    self._evidence_log.create_event(
                        "decision",
                        {
                            "phase": "CHAIN_SNAPSHOT",
                            "blake3_hex": _blake3_hex,
                            "sha256_hex": _sha256_hex,
                            "counter_snapshot": _snap,
                            "sprint_id": getattr(self, "_sprint_id", ""),
                        },
                        confidence=1.0,
                    )
                    self._prev_chain_hash = _blake3_hex
                except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    if self._evidence_log is not None:
                        try:
                            self._evidence_log.create_event(
                                "error", {"phase": "CHAIN_SNAPSHOT_FAILED", "error": str(_e)}, confidence=0.0
                            )
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
            await self._close_dedup()
            if self._rel_discovery_engine is not None:
                try:
                    from hledac.universal.paths import LMDB_ROOT

                    self._rel_discovery_engine.save_graph(LMDB_ROOT / "rel_discovery_graph.pkl")
                    log.debug("[RelDiscovery] Graph saved at teardown")
                    self._sync_latent_relationships_to_graph()
                except Exception as _e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                    log.warning(f"[RelDiscovery] save/sync failed: {_e}")
            if self._enrichment_services:
                await self._enrichment_services.close()
                if ENV.get_bool("HLEDAC_ENABLE_PRIVACY_LAYER"):
                    try:
                        _privacy = self._privacy_layer or getattr(self._layer_manager, "privacy", None)
                        if _privacy and hasattr(self, "_privacy_context_id") and self._privacy_context_id:
                            await _privacy.close_privacy_context(self._privacy_context_id)
                    except Exception as _e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                        logger.debug("privacy_context close failed: %s", _e)
            _sidecar_task = safe_create_task(self._sidecar_orchestrator.run_advisory_runner())
            _sidecar_task.add_done_callback(
                lambda t: log.debug(
                    "[F290] advisory_runner done: %s", t.exception() if t.done() and t.exception() else "ok"
                )
            )
            await self._run_ane_semantic_dedup_advisory()
            self._maybe_launch_enhanced_research()
            await self._unload_hermes_at_teardown()
            from hledac.universal.brain import _lazy as lazy_module

            lazy_module.unload_all()
            for t in list(self._bg_tasks):
                t.cancel()
            if self._bg_tasks:
                await safe_gather_fire_and_forget(*self._bg_tasks, label="sprint_scheduler:7141")
            self._bg_tasks.clear()
            if self._sidecar_tasks:
                _pending = list(self._sidecar_tasks)
                try:
                    async with asyncio.timeout(15.0):
                        await safe_gather_fire_and_forget(*_pending, label="sprint_scheduler:sidecar_tasks")
                except* TimeoutError as e:
                    # PEP 654: asyncio.timeout() raises BaseExceptionGroup, not TimeoutError.
                    log.debug("[F265C] Sidecar tasks did not complete in 15s: %s", e)
                    for t in _pending:
                        if not t.done():
                            t.cancel()
                finally:
                    self._sidecar_tasks.clear()
            if self._dht_node is not None:
                try:
                    await self._dht_node.stop()
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
                self._dht_node = None
            if self._i2p_transport is not None:
                try:
                    await self._i2p_transport.stop()
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
                self._i2p_transport = None
            if self._nym_transport is not None:
                try:
                    await self._nym_transport.stop()
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
                self._nym_transport = None
            if self._tor_transport is not None:
                try:
                    await self._tor_transport.stop()
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
                self._tor_transport = None
            await self._close_metrics_registry()
            if hasattr(self._fetch_coordinator, "reset_cover_count"):
                self._fetch_coordinator.reset_cover_count()
            try:
                from hledac.universal.fetching import public_fetcher

                await public_fetcher._close_tor_session()
                await public_fetcher._close_i2p_session()
            except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                pass
            try:
                from hledac.universal.transport import get_gopher_transport

                await get_gopher_transport().stop()
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
            try:
                from hledac.universal.intelligence import get_open_source_collectors

                await get_open_source_collectors().close()
            except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                pass
            _r = self._result
            match ():
                case _ if _r.public_backend_degraded:
                    _r.dominant_public_blocker = "backend_degraded"
                case _ if _r.public_error and _r.public_error not in ("", "null"):
                    _r.dominant_public_blocker = _r.public_error[:80]
                case _:
                    pass
            match ():
                case _ if _r.feed_inaccessible_detected:
                    _r.dominant_feed_blocker = "feed_inaccessible"
                case _ if _r.feed_content_empty_detected:
                    _r.dominant_feed_blocker = "feed_content_empty"
                case _ if _r.feed_no_pattern_with_content:
                    _r.dominant_feed_blocker = "feed_no_pattern_with_content"
                case _ if _r.findings_build_loss_detected:
                    _r.dominant_feed_blocker = "findings_build_loss"
                case _ if _r.feed_zero_yield_detected:
                    _r.dominant_feed_blocker = "feed_zero_yield"
                case _:
                    pass
            match ():
                case _ if _r.dominant_public_blocker and (not _r.dominant_feed_blocker):
                    _r.dominant_branch_blocker = "public"
                case _ if _r.dominant_feed_blocker and (not _r.dominant_public_blocker):
                    _r.dominant_branch_blocker = "feed"
                case _ if _r.dominant_public_blocker and _r.dominant_feed_blocker:
                    _r.dominant_branch_blocker = "both"
                case _:
                    pass
            _tags: list[str] = []
            if _r.public_backend_degraded:
                _tags.append("public_degraded")
            if _r.feed_inaccessible_detected:
                _tags.append("feed_inaccessible")
            if _r.feed_content_empty_detected:
                _tags.append("feed_content_empty")
            if _r.feed_no_pattern_with_content:
                _tags.append("feed_no_pattern")
            if _r.findings_build_loss_detected:
                _tags.append("findings_build_loss")
            if _r.feed_zero_yield_detected:
                _tags.append("feed_zero_yield")
            if _tags:
                _r.branch_degradation_summary = "_".join(_tags)
            _rl_lane_combo: frozenset[str] = frozenset()
            if self._policy_manager is not None:
                try:
                    self._policy_manager.update(self._result)
                except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    log.debug(f"[SprintPolicyManager] update() failed: {e}")
                try:
                    _action = self._policy_manager.get_action()
                    if _action is not None and _action >= 10:
                        from rl.actions import LANE_COMBINATIONS, lane_combo_from_action

                        _combo_idx = lane_combo_from_action(_action)
                        if _combo_idx is not None and _combo_idx < len(LANE_COMBINATIONS):
                            _rl_lane_combo = LANE_COMBINATIONS[_combo_idx]
                            self._result.rl_lane_combo = _rl_lane_combo
                            log.debug("[F265LANE] RL lane combo: %s", _rl_lane_combo)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            _pivot_policy_suggestions: list[dict] = []
            if self._policy_manager is not None:
                try:
                    _pivot_policy_suggestions = self._policy_manager.suggest_next_pivot([], None)
                    if _pivot_policy_suggestions:
                        first = _pivot_policy_suggestions[0]
                        if first.get("pivot_type") == "dark_surface":
                            log.info("RL suggests dark_surface pivot: %s", first.get("reason", ""))
                        self._result.rl_suggested_pivot = first.get("pivot_type", "unknown")
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            if self._policy_manager is not None:
                try:
                    _rl_telemetry = self._policy_manager.get_telemetry()
                    self._result.rl_enabled = _rl_telemetry.get("rl_enabled", False)
                    self._result.rl_epsilon = _rl_telemetry.get("rl_epsilon", 0.0)
                    self._result.rl_total_reward = _rl_telemetry.get("rl_total_reward", 0.0)
                    self._result.rl_last_action = _rl_telemetry.get("rl_last_action", 0)
                except Exception:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
                    pass
            try:
                self._adapt_source_weights_from_feedback()
            except Exception as e:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
                log.debug(f"[F199A] _adapt_source_weights_from_feedback() failed: {e}")
            if self._policy_manager is not None and self._policy_manager.enabled:
                _decisions: list = []
                _feed_url = "feed"
                try:
                    for _feed, _fb in self._source_quality_feedback.items():
                        _total = _fb.get("fetched", 0)
                        _accepted = _fb.get("accepted", 0)
                        if _total == 0:
                            continue
                        _ratio = _accepted / _total if _total > 0 else 0.0
                        _decisions.append({"accepted": _ratio >= 0.15, "source_family": _feed})
                    if _decisions:
                        try:
                            self._policy_manager.update_with_quality_decisions(_decisions, feed_url=_feed_url)
                            self._result.policy_quality_feedback_calls += 1
                            self._result.policy_quality_feedback_decisions += len(_decisions)
                            self._result.policy_quality_feedback_sources += len(
                                {d.get("source_family") for d in _decisions}
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            self._result.policy_quality_feedback_errors += 1
                            log.debug(f"[F228A] policy_quality_feedback update failed: {_e}")
                except asyncio.CancelledError:
                    raise
                except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    self._result.policy_quality_feedback_errors += 1
                    log.debug(f"[F228A] policy_quality_feedback decision collection failed: {_e}")
            await self._ensure_nonfeed_predispatch_before_finalization(query, "run_complete")
            self._capture_timing_fields()
            if not self._result.early_exit_class:
                await self._finalize_result_truth(
                    exit_path="run_complete", exit_reason="run() finished normally", exit_phase="TEARDOWN", query=query
                )
            try:
                self._result.timer_events = self._timer.events
            except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                self._result.timer_events = None
            try:
                from hledac.universal.knowledge.duckdb_store import get_arrow_metrics

                self._result.arrow_metrics = get_arrow_metrics()
            except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                self._result.arrow_metrics = {}
        except Exception as _run_err:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            _exc_name = type(_run_err).__name__
            _exc_str = str(_run_err)
            _exc_str_lower = _exc_str.lower()
            match True:
                case _ if "Timeout" in _exc_name or "timeout" in _exc_str:
                    _run_error_class = "timeout_error"
                case _ if "DuckDB" in _exc_name or "duckdb" in _exc_str_lower:
                    _run_error_class = "storage_error"
                case _ if "LMDB" in _exc_name or "lmdb" in _exc_str_lower:
                    _run_error_class = "storage_error"
                case _ if "LanceDB" in _exc_name or "lance" in _exc_str_lower:
                    _run_error_class = "storage_error"
                case _ if "MLX" in _exc_name or "mlx" in _exc_str_lower:
                    _run_error_class = "mlx_error"
                case _ if "Memory" in _exc_name or "memory" in _exc_str_lower:
                    _run_error_class = "mlx_error"
                case _ if "Network" in _exc_name or "network" in _exc_str_lower:
                    _run_error_class = "network_error"
                case _ if "Connection" in _exc_name or "connection" in _exc_str_lower:
                    _run_error_class = "network_error"
                case _ if "HTTP" in _exc_name or "http" in _exc_str_lower:
                    _run_error_class = "network_error"
                case _ if "Validation" in _exc_name or "validation" in _exc_str_lower:
                    _run_error_class = "validation_error"
                case _ if "Cancelled" in _exc_name:
                    _run_error_class = "cancelled"
                case _:
                    _run_error_class = "unknown_error"
            _run_error = _run_err
            self._result.run_error_class = _run_error_class
            self._result.run_error = str(_run_err)[:500]
            raise
        try:
            if self._communication_layer is not None:
                try:
                    _broadcast = getattr(self._communication_layer, "broadcast_message", None)
                    if _broadcast is not None:
                        _result_findings = getattr(self._result, "findings", []) if self._result is not None else []
                        _summary = {
                            "event": "sprint_end",
                            "sprint_id": self.sprint_id,
                            "findings": len(_result_findings),
                        }
                        if asyncio.iscoroutine(_broadcast(_summary)):
                            await _broadcast(_summary)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
        except Exception as _broadcast_layer_exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            logger.debug(f"[F26X-3] communication layer post-sprint broadcast failed: {_broadcast_layer_exc}")
        try:
            _shutdown_evt = getattr(self, "_duckdb_writer_shutdown", None)
            if _shutdown_evt is not None:
                _shutdown_evt.set()
                _writer_wakeup = getattr(self, "_writer_wakeup", None)
                if _writer_wakeup is not None:
                    _writer_wakeup.set()
            _writer_task = getattr(self, "_duckdb_writer_task", None)
            if _writer_task is not None:
                try:
                    async with asyncio.timeout(2.0):
                        await _writer_task
                except TimeoutError:
                    if not _writer_task.done():
                        _writer_task.cancel()
                except asyncio.CancelledError:
                    if not _writer_task.done():
                        _writer_task.cancel()
        except Exception as _writer_shutdown_exc:  # noqa: BLE001 — best-effort; export/write failure; non-critical
            logger.debug("[F285] writer shutdown failed: %s", _writer_shutdown_exc)
        try:
            _mp_shutdown = getattr(self, "_memory_pressure_shutdown", None)
            if _mp_shutdown is not None:
                _mp_shutdown.set()
        except Exception as _mp_shutdown_exc:  # noqa: BLE001 — best-effort; memory operation; non-critical
            logger.debug("[MEM-012] memory pressure loop shutdown failed: %s", _mp_shutdown_exc)
        return self._result

    async def _record_scheduler_exit(self, path: str, reason: str, phase: str | None = None) -> None:
        """

        Sprint F207V-A: Record the exact exit path taken by the scheduler.



        Side-effect light -- only updates in-memory telemetry fields.

        No network, no DB write, no graph write.

        """
        self._result.scheduler_exit_path = path
        self._result.scheduler_exit_reason = reason
        self._result.scheduler_exit_phase = phase
        self._result.scheduler_exit_elapsed_s = _time.monotonic() - self._run_started_at
        self._result.scheduler_exit_cycle = self._result.cycles_started
        if not getattr(self._result, "return_guard_checked", False):
            try:
                await self._ensure_mandatory_nonfeed_before_return(
                    getattr(self, "_query", "") or "", None, f"scheduler_exit_capture({path})"
                )
            except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                pass
        self._result.scheduler_exit_guard_checked = self._result.return_guard_checked
        self._result.scheduler_exit_guard_required = self._result.return_guard_required_lanes
        self._result.scheduler_exit_guard_satisfied = self._result.return_guard_satisfied

    def _capture_timing_fields(self) -> None:
        """

        Sprint F215D: Capture wall-clock timing fields for early-exit classification.



        Called before _finalize_result_truth in early-exit break paths so that

        _compute_early_exit_class has correct elapsed_pct (not 0.0).

        Timing is also captured at the normal-completion path (lines 1843-1859).

        """
        self._result.requested_duration_s = self._config.sprint_duration_s
        self._result.actual_duration_s = _time.monotonic() - self._wall_clock_start
        self._result.elapsed_pct = (
            self._result.actual_duration_s / self._result.requested_duration_s
            if self._result.requested_duration_s > 0
            else 0.0
        )
        _windup = self._config.effective_windup_lead_s
        _pre_loop = getattr(self._result, "pre_loop_elapsed_s", 0.0) or 0.0
        self._result.active_window_budget_s = max(30.0, self._config.sprint_duration_s - _windup - _pre_loop)
        _total_window = self._config.effective_windup_lead_s + self._result.active_window_budget_s
        _windup_eff = self._config.effective_windup_lead_s / _total_window if _total_window > 0 else 0.0
        self._result.windup_efficiency = _windup_eff
        if _windup_eff > 0.4:
            logger.warning(
                "[F289-WINDUP] windup_efficiency=%.2f (>0.40 critical) — windup=%.0fs, active=%.0fs, total=%.0fs. Consider reducing windup lead.",
                _windup_eff,
                self._config.effective_windup_lead_s,
                self._result.active_window_budget_s,
                _total_window,
            )
        _windup_actual = self._config.effective_windup_lead_s
        _pre_loop_cost = getattr(self._result, "pre_loop_elapsed_s", 0.0) or 0.0
        _active_elapsed = max(0.0, self._result.actual_duration_s - _windup_actual - _pre_loop_cost)
        self._result.active_window_elapsed_s = _active_elapsed

    def _compute_early_exit_class(self, exit_path: str) -> tuple[str, str]:
        """

        Sprint F215D: Compute canonical early exit classification.



        Called in _finalize_result_truth after timing fields are populated.

        Returns (early_exit_class, early_exit_reason).



        Invariants (GHOST_INVARIANTS):

          - No network I/O, no model load, no browser launch

          - Fail-safe: returns (COMPLETED_FULL_DURATION, "") on any error

        """
        try:
            try:
                from metrics_registry import get_metrics_registry

                get_metrics_registry().inc("windup_entry_count")
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
            r = self._result
            _exit_override = getattr(self, "_run_exit_path_override", None)
            if _exit_override:
                exit_path = _exit_override
            _is_early_exit = (
                exit_path
                in (
                    "early_complete_return_guard_satisfied",
                    "early_complete_feed_only",
                    "early_complete_prelude_complete",
                    "early_complete_no_work_remaining",
                )
                or r.elapsed_pct < 0.5
            )
            if _is_early_exit:
                try:
                    from metrics_registry import get_metrics_registry

                    get_metrics_registry().inc("windup_early_exit_count")
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            if r.hard_deadline_exceeded or exit_path == "hard_deadline_exceeded":
                return (EarlyExitClass.ABORTED_BY_DEADLINE, "hard deadline exceeded before planned duration")
            if r.aborted and r.abort_reason == "domain_detected_no_seeds":
                return (
                    EarlyExitClass.EARLY_COMPLETE_NO_WORK_REMAINING,
                    "domain_detected_no_seeds: no seeds for CT/DOH/WAYBACK lanes",
                )
            if r.aborted or exit_path == "aborted_by_error":
                return (EarlyExitClass.ABORTED_BY_ERROR, r.abort_reason or "scheduler exception")
            if hasattr(r, "peak_rss_gib") and r.peak_rss_gib > 0:
                if r.elapsed_pct < 0.5 and r.budget_violations > 0:
                    return (
                        EarlyExitClass.ABORTED_BY_MEMORY,
                        f"memory pressure ({r.budget_violations} budget violations)",
                    )
            if r.elapsed_pct >= 0.9:
                return (
                    EarlyExitClass.COMPLETED_FULL_DURATION,
                    f"ran to planned duration ({r.actual_duration_s:.1f}s / {r.requested_duration_s:.1f}s)",
                )
            nonfeed_accepted = (
                r.lane_ct_accepted_findings
                + r.lane_wayback_accepted_findings
                + r.lane_pdns_accepted_findings
                + r.lane_blockchain_accepted_findings
                + r.lane_ipfs_accepted_findings
                + r.lane_doh_accepted_findings
            )
            feed_accepted = r.accepted_findings - nonfeed_accepted
            _guard = FeedDominanceGuard(strict=self._config.require_nonfeed_corrob_for_early_exit).compute(
                total_accepted=r.accepted_findings, feed_accepted=feed_accepted, nonfeed_accepted=nonfeed_accepted
            )
            r.feed_dominance_ratio = _guard.feed_dominance_ratio
            r.feed_dominance_class = _guard.feed_dominance_class
            r.feed_dominance_guard_triggered = _guard.guard_triggered
            r.should_recommend_nonfeed_diagnostic = _guard.should_recommend_nonfeed_diagnostic
            if r.accepted_findings > 0 and nonfeed_accepted == 0:
                _base_reason = f"feed-only early exit ({r.accepted_findings} accepted findings, no nonfeed)"
                if _guard.guard_triggered:
                    _base_reason += (
                        f" | feed-dominant:ratio={_guard.feed_dominance_ratio:.3f}:recommend nonfeed_diagnostic"
                    )
                return (EarlyExitClass.EARLY_COMPLETE_FEED_ONLY, _base_reason)
            if r.return_guard_satisfied and exit_path in (
                "windup_barrier_passed",
                "windup_barrier_break",
                "post_sleep_windup_break",
            ):
                return (
                    EarlyExitClass.EARLY_COMPLETE_RETURN_GUARD_SATISFIED,
                    f"return guard satisfied, early windup ({exit_path})",
                )
            if exit_path in (
                "stop_requested_break",
                "stop_on_first_accepted_break",
                "cycles_limit_reached",
                "max_cycles_break",
            ):
                return (EarlyExitClass.EARLY_COMPLETE_NO_WORK_REMAINING, f"no work remaining ({exit_path})")
            if exit_path == "prelude_complete" and r.elapsed_pct < 0.9:
                pct = r.elapsed_pct * 100
                return (
                    EarlyExitClass.EARLY_COMPLETE_PRELUDE_COMPLETE,
                    f"prelude early exit ({pct:.0f}% of planned duration, exit_path={exit_path})",
                )
            pct = r.elapsed_pct * 100
            return (
                EarlyExitClass.EARLY_COMPLETE_NO_WORK_REMAINING,
                f"early exit ({pct:.0f}% of planned duration, exit_path={exit_path})",
            )
        except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning("_finalize_result_truth failed: %s", _exc)
            return (EarlyExitClass.COMPLETED_FULL_DURATION, "")

    async def _finalize_result_truth(self, exit_path: str, exit_reason: str, exit_phase: str, query: str = "") -> None:
        """

        Sprint F208I-B: Finalize SprintSchedulerResult before run() returns.



        Computes terminality from acquisition strategy and records scheduler exit

        path. Called once before every return from run() -- both normal completion

        and all early exit paths (stop_requested, abort, windup_barrier, etc.).



        Invariants (GHOST_INVARIANTS):

          - No network I/O

          - No model/MLX load

          - No browser launch

          - No blocking ops

          - Fail-safe: terminality errors don't prevent return

        """
        try:
            uma_state = "ok"
            swap_detected = False
            if self._governor is not None:
                try:
                    _snap = await self._governor.evaluate()
                    uma_state = getattr(_snap, "uma_state", "ok")
                    swap_detected = getattr(_snap, "swap_detected", False)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            try:
                from metrics_registry import get_metrics_registry

                _reg = get_metrics_registry()
                _total_findings = sum(
                    [
                        self._result.lane_ct_accepted_findings,
                        self._result.lane_public_accepted_findings,
                        self._result.lane_wayback_accepted_findings,
                        self._result.lane_pdns_accepted_findings,
                        self._result.lane_blockchain_accepted_findings,
                        self._result.lane_ipfs_accepted_findings,
                        self._result.lane_doh_accepted_findings,
                    ]
                )
                _yield = _total_findings / max(self._result.actual_duration_s, 1.0)
                _encoded = f"{uma_state}|{_yield:.4f}|{getattr(self._result, 'elapsed_pct', 0.0):.2f}"
                _reg.set_gauge("memory_pressure_vs_finding_yield", float(hash(_encoded) % 10000) / 100.0)
            except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
                pass
            _mlt_required = required_terminal_lanes(
                snapshot=self._acquisition_plan, query=query, uma_state=uma_state, swap_detected=swap_detected
            )
            _observed_outcomes: list[dict] = []
            _seen_outcome_lanes: set[str] = set()
            if self._public_outcome is not None:
                _observed_outcomes.append(self._public_outcome)
                _seen_outcome_lanes.add("PUBLIC")
            else:
                for _mlt in _mlt_required:
                    if _mlt.lane == AcquisitionLane.PUBLIC and _mlt.required:
                        _skip_reason = "public_branch_not_run"
                        if self._acquisition_plan is not None:
                            for _plan in self._acquisition_plan.plans or ():
                                if hasattr(_plan, "lane") and _plan.lane == AcquisitionLane.PUBLIC:
                                    _reason = getattr(_plan, "reason", "") or ""
                                    if _reason:
                                        _skip_reason = _reason
                                    break
                        _observed_outcomes.append(
                            {
                                "lane": "PUBLIC",
                                "family": "PUBLIC",
                                "attempted": False,
                                "skipped": True,
                                "terminal_state": "skipped",
                                "skip_reason": _skip_reason,
                                "raw_count": 0,
                                "built_count": 0,
                                "accepted_count": 0,
                                "error": None,
                                "timeout": False,
                                "duration_s": None,
                            }
                        )
                        _seen_outcome_lanes.add("PUBLIC")
                        break
            _ct_outcome = self._collect_ct_terminal_outcome()
            if _ct_outcome is not None:
                _observed_outcomes.append(_ct_outcome)
                _seen_outcome_lanes.add("CT")
            else:
                for _mlt in _mlt_required:
                    if _mlt.lane == AcquisitionLane.CT and _mlt.required:
                        _skip_reason = "ct_required_not_attempted"
                        if self._acquisition_plan is not None:
                            for _plan in self._acquisition_plan.plans or ():
                                if hasattr(_plan, "lane") and _plan.lane == AcquisitionLane.CT:
                                    _reason = getattr(_plan, "reason", "") or ""
                                    if "swap" in _reason or "hardware_critical" in _reason:
                                        _skip_reason = _reason
                                    elif not getattr(_plan, "enabled", True):
                                        _skip_reason = _reason or "lane_disabled"
                                    break
                        _observed_outcomes.append(
                            {
                                "lane": "CT",
                                "family": "CT",
                                "attempted": False,
                                "skipped": True,
                                "terminal_state": "skipped",
                                "skip_reason": _skip_reason,
                                "raw_count": 0,
                                "accepted_count": 0,
                                "error": None,
                                "timeout": False,
                            }
                        )
                        _seen_outcome_lanes.add("CT")
                        break
            for _o in self._result.acquisition_lane_outcomes or ():
                _lane_name: str | None = getattr(_o, "lane", None)
                if _lane_name is None and isinstance(_o, dict):
                    _lane_name = _o.get("lane")
                if _lane_name and _lane_name not in _seen_outcome_lanes:
                    _observed_outcomes.append(_o.to_dict() if hasattr(_o, "to_dict") else dict(_o))
                    _seen_outcome_lanes.add(_lane_name)
            _term_report = terminality_report(required_lanes=_mlt_required, observed_outcomes=tuple(_observed_outcomes))
            self._result.acquisition_terminality_checked = True
            self._result.acquisition_terminality_satisfied = len(_term_report.get("missing_lanes", [])) == 0
            self._result.acquisition_terminality_missing_lanes = tuple(_term_report.get("missing_lanes", []))
            self._result.acquisition_terminality_report = _term_report
        except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F208I-B] acquisition_terminality check failed: %s", exc)
            self._result.acquisition_terminality_checked = True
            self._result.acquisition_terminality_satisfied = False
            self._result.acquisition_terminality_missing_lanes = ()
            self._result.acquisition_terminality_report = {"error": str(exc)}
        _exit_class, _exit_reason = self._compute_early_exit_class(exit_path)
        self._result.early_exit_class = _exit_class
        self._result.early_exit_reason = _exit_reason
        await self._record_scheduler_exit(exit_path, exit_reason, exit_phase)
        _pub_stage, _pub_counters = _compute_public_stage(self._public_outcome, self._public_pipeline_result)
        self._result.public_terminal_stage = _pub_stage
        self._result.public_stage_counters = _pub_counters
        if self._public_pipeline_result is not None:
            self._result.public_discovery_empty_reason = (
                getattr(self._public_pipeline_result, "public_discovery_empty_reason", "") or ""
            )
        _acq_profile = getattr(self._config, "acquisition_profile", "default")
        if NonfeedMissionController.is_mission_profile(_acq_profile):
            try:
                _mission_snapshot = NonfeedMissionController.build_snapshot(
                    acquisition_profile=_acq_profile,
                    acquisition_lane_outcomes=self._result.acquisition_lane_outcomes or (),
                    public_outcome=self._public_outcome,
                    ct_quarantine_count=self._result.ct_quarantine_count,
                    quality_rejection_ledger=self._result.quality_rejection_ledger or (),
                )
                self._result.nonfeed_mission_active = _mission_snapshot.mission_active
                self._result.nonfeed_required_families = _mission_snapshot.required_families
                self._result.nonfeed_optional_families = _mission_snapshot.optional_families
                self._result.nonfeed_family_status = _mission_snapshot.family_status
                self._result.nonfeed_all_required_terminal = _mission_snapshot.all_required_terminal
                self._result.nonfeed_any_accepted = _mission_snapshot.any_accepted
                self._result.nonfeed_provider_failures = _mission_snapshot.provider_failures
                self._result.nonfeed_memory_skips = _mission_snapshot.memory_skips
                self._result.nonfeed_mission_exit_reason = _mission_snapshot.mission_exit_reason
                if hasattr(self, "_nonfeed_ledger") and self._nonfeed_ledger is not None:
                    self._result.nonfeed_candidate_ledger_summary = self._nonfeed_ledger.summary()
            except Exception as _exc:  # noqa: BLE001 — best-effort; memory operation; non-critical
                log.debug("[F217B] NonfeedMissionController snapshot failed: %s", _exc)
        try:
            from hledac.universal.transport.conditional_cache import get_stats as _cc_stats
            from hledac.universal.transport.http3_lane import get_stats as _h3_stats
            from hledac.universal.transport.prewarm_pool import get_stats as _pw_stats

            self._result.transport_efficiency = {
                "http3_enabled": _h3_stats().get("enabled", 0),
                "http3_altsvc_hits": _h3_stats().get("altsvc_hits", 0),
                "http3_altsvc_misses": _h3_stats().get("altsvc_misses", 0),
                "http3_altsvc_records": _h3_stats().get("altsvc_records", 0),
                "http3_cache_size": _h3_stats().get("cache_size", 0),
                "http3_cache_hits": _h3_stats().get("cache_hit", 0),
                "conditional_cache_hits": _cc_stats().get("lookup_hits", 0),
                "conditional_cache_misses": _cc_stats().get("lookup_misses", 0),
                "conditional_cache_errors": _cc_stats().get("lookup_errors", 0),
                "conditional_cache_304s": _cc_stats().get("conditional_304s", 0),
                "conditional_sends": _cc_stats().get("conditional_sends", 0),
                "prewarm_sessions_created": _pw_stats().get("sessions_created", 0),
                "prewarm_hit_rate": _pw_stats().get("round_robin_hits", 0),
            }
        except Exception as _exc:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
            log.debug("[F265B] transport_efficiency telemetry failed: %s", _exc)

    async def _ensure_nonfeed_predispatch_before_finalization(self, query: str, reason: str) -> None:
        """

        Sprint F208M-A: Ensure nonfeed predispatch has run before final terminality.



        This helper is called before every _finalize_result_truth() to guarantee

        that bounded CT/PUBLIC predispatch has had a chance to populate

        acquisition_lane_outcomes / _lane_outcomes BEFORE terminality is computed.



        Without this, terminality computed in _finalize_result_truth sees

        acquisition_lane_outcomes empty (no CT attempted yet), marking CT as

        missing even though _maybe_dispatch_nonfeed_probe_lanes() was called.



        Runs only once per sprint -- subsequent calls are no-ops.

        Records explicit telemetry so failure is never silent.



        Args:

            query: Sprint query for lane shaping.

            reason: Human-readable reason for this finalization call.



        Raises:

            CancelledError: propagated if predispatch is cancelled.

        """
        if self._result.nonfeed_predispatch_checked:
            return
        self._result.nonfeed_predispatch_checked = True
        self._result.nonfeed_predispatch_reason = reason
        try:
            await self._maybe_dispatch_nonfeed_probe_lanes(query, self._duckdb_store)
            self._result.nonfeed_predispatch_ran = True
            _count = len(self._result.acquisition_lane_outcomes or ())
            self._result.nonfeed_predispatch_outcomes_count = _count
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F208M-A] Nonfeed predispatch failed before finalization: %s", exc)
            self._result.nonfeed_predispatch_ran = False
            self._result.nonfeed_predispatch_outcomes_count = 0

    def _collect_ct_terminal_outcome(self) -> dict | None:
        """

        Sprint F208L-A: Collect canonical CT terminal outcome from all CT surfaces.



        This is the ONE source of truth for CT terminality in _finalize_result_truth.

        It inspects all canonical CT surfaces and returns a complete outcome dict

        with lane, family, attempted, terminal_state, raw_count, accepted_count,

        error, timeout, skipped fields.



        Returns None when CT was never attempted (not even attempted=True with zero

        raw results) -- allowing terminality_report to mark CT as missing.



        Terminal state rules:

          - error not None  -> terminal_state="error"

          - timeout=True    -> terminal_state="timeout"

          - skipped=True    -> terminal_state="skipped"

          - raw_count > 0 and accepted_count == 0 -> terminal_state="success_empty"

          - raw_count == 0 and attempted=True and no error -> terminal_state="empty"

          - attempted=True (default terminal) -> terminal_state="success"

        """
        _ct_outcome: dict | None = None
        for _o in self._result.acquisition_lane_outcomes or ():
            _lane_name = getattr(_o, "lane", None)
            if _lane_name is None and isinstance(_o, dict):
                _lane_name = _o.get("lane")
            if _lane_name == "CT":
                _d = _o.to_dict() if hasattr(_o, "to_dict") else dict(_o)
                _attempted = _d.get("attempted", False)
                if not _attempted:
                    continue
                _raw = _d.get("raw_count", 0) or _d.get("ct_results_raw", 0) or 0
                _accepted = _d.get("accepted_count", 0) or _d.get("accepted_findings", 0) or 0
                _error = _d.get("error")
                _timeout = _d.get("timeout", False)
                _skipped = _d.get("skipped", False)
                if _error is not None:
                    _ts = "error"
                elif _timeout:
                    _ts = "timeout"
                elif _skipped:
                    _ts = "skipped"
                elif _raw > 0 and _accepted == 0:
                    _ts = "success_empty"
                elif _raw == 0 and _attempted and (_error is None):
                    _ts = "empty"
                else:
                    _ts = "success"
                _ct_outcome = {
                    "lane": "CT",
                    "family": "CT",
                    "attempted": True,
                    "terminal_state": _ts,
                    "raw_count": _raw,
                    "accepted_count": _accepted,
                    "error": _error,
                    "timeout": _timeout,
                    "skipped": _skipped,
                }
                break
        if _ct_outcome is None:
            for _o in getattr(self, "_lane_outcomes", None) or ():
                _lane_name = getattr(_o, "lane", None)
                if _lane_name is None and isinstance(_o, dict):
                    _lane_name = _o.get("lane")
                if _lane_name == "CT":
                    _d = _o.to_dict() if hasattr(_o, "to_dict") else dict(_o)
                    _attempted = _d.get("attempted", False)
                    if not _attempted:
                        continue
                    _raw = _d.get("raw_count", 0) or _d.get("ct_results_raw", 0) or 0
                    _accepted = _d.get("accepted_count", 0) or _d.get("accepted_findings", 0) or 0
                    _error = _d.get("error")
                    _timeout = _d.get("timeout", False)
                    _skipped = _d.get("skipped", False)
                    if _error is not None:
                        _ts = "error"
                    elif _timeout:
                        _ts = "timeout"
                    elif _skipped:
                        _ts = "skipped"
                    elif _raw > 0 and _accepted == 0:
                        _ts = "success_empty"
                    elif _raw == 0 and _attempted and (_error is None):
                        _ts = "empty"
                    else:
                        _ts = "success"
                    _ct_outcome = {
                        "lane": "CT",
                        "family": "CT",
                        "attempted": True,
                        "terminal_state": _ts,
                        "raw_count": _raw,
                        "accepted_count": _accepted,
                        "error": _error,
                        "timeout": _timeout,
                        "skipped": _skipped,
                    }
                    break
        if _ct_outcome is None:
            _disc = getattr(self._result, "ct_log_discovered", 0) or 0
            _acc = getattr(self._result, "lane_ct_accepted_findings", 0) or 0
            _ct_adapter_called = getattr(self._ct_log_client, "_called", False)
            if _disc > 0 or _acc > 0 or _ct_adapter_called:
                _raw = _disc
                if _raw > 0 and _acc == 0:
                    _ts = "success_empty"
                elif _raw == 0 and _ct_adapter_called and (_acc == 0):
                    _ts = "empty"
                else:
                    _ts = "success"
                _ct_outcome = {
                    "lane": "CT",
                    "family": "CT",
                    "attempted": True,
                    "terminal_state": _ts,
                    "raw_count": _raw,
                    "accepted_count": _acc,
                    "error": None,
                    "timeout": False,
                    "skipped": False,
                }
        return _ct_outcome

    def _final_source_family_outcomes_for_terminality(self) -> tuple[dict, ...]:
        """

        Sprint F210A: Canonical source family outcomes for terminality SSOT.



        This mirrors the EXACT same logic used in _build_diagnostic_report to build

        source_family_outcomes (lines ~6219-6244), ensuring terminality_report is

        ALWAYS computed from the same canonical outcomes that go into the report.



        This fixes the stale terminality bug where:

          - _finalize_result_truth() is called before all nonfeed lanes complete

          - terminality was computed from a snapshot with CT/PUBLIC not yet attempted

          - source_family_outcomes reflected final state but terminality was stale



        Returns:

            Tuple of outcome dicts for terminality computation -- same format as

            observed_outcomes passed to terminality_report().

        """
        _outcomes: list[dict] = []
        _seen: set[str] = set()
        if self._public_outcome is not None:
            _outcomes.append(self._public_outcome)
            _seen.add("PUBLIC")
        _ct_outcome = self._collect_ct_terminal_outcome()
        if _ct_outcome is not None:
            _outcomes.append(_ct_outcome)
            _seen.add("CT")
        for _fam, _lane in [
            ("ct", AcquisitionLane.CT),
            ("wayback", AcquisitionLane.WAYBACK),
            ("passive_dns", AcquisitionLane.PASSIVE_DNS),
            ("blockchain", AcquisitionLane.BLOCKCHAIN),
            ("feed", "FEED"),
            ("public", AcquisitionLane.PUBLIC),
        ]:
            if _lane == "PUBLIC":
                continue
            if _lane == AcquisitionLane.CT:
                continue
            _raw: dict | list | None = None
            if _lane == "FEED":
                _raw = getattr(self, "_feed_verdicts", []) or None
                if _raw is not None:
                    _feed_accepted = (
                        (self._result.accepted_findings or 0)
                        - (self._result.public_accepted_findings or 0)
                        - (self._result.ct_log_accepted_findings or 0)
                    )
                    _feed_accepted = max(0, _feed_accepted)
                    if isinstance(_raw, list) and _raw:
                        _first = _raw[0]
                        if isinstance(_first, tuple):
                            _raw = {
                                "verdict": _first,
                                "accepted_count": _feed_accepted,
                                "raw_count": _first[1] if len(_first) > 1 else 0,
                                "attempted": True,
                            }
            elif self._lane_outcomes:
                for _o in self._lane_outcomes:
                    if hasattr(_o, "lane") and _o.lane == _lane:
                        _raw = _o
                        break
            _normalized = normalize_source_family_outcome(_fam, _raw if isinstance(_raw, dict) else {})
            _lane_name = _normalized.get("lane") or _fam.upper()
            _normalized["lane"] = _lane_name
            if _lane_name not in _seen:
                _outcomes.append(_normalized)
                _seen.add(_lane_name)
        return tuple(canonicalize_source_family_outcomes(_outcomes))

    async def _maybe_dispatch_nonfeed_probe_lanes(self, query: str, duckdb_store: Any) -> None:
        """

        Sprint F207M-A: Bounded nonfeed pre-dispatch checkpoint.



        Fires before the first active cycle's aggressive branch fan-out can trigger

        early windup, ensuring CT (and optionally WAYBACK/PASSIVE_DNS) are attempted

        at least once for domain queries before the sprint winds down.



        Invariants (strict):

          - No stealth, no graph writes, no unbounded network

          - max_items <= 5, timeout_s <= 15

          - Fail-soft: errors/skips are telemetry only, never crash sprint

          - CT only by default for domain queries

          - WAYBACK/PASSIVE_DNS only when memory is ok/warn



        Windup blocking:

          If domain query + CT enabled but not yet attempted, set

          windup_blocked_until_nonfeed_attempted = True so the windup gate

          delays entry until pre-dispatch completes.

        """
        import time as _time

        if self._nonfeed_predispatch_done:
            return
        if self._acquisition_plan is None:
            return
        ct_plan = get_lane_plan(self._acquisition_plan, AcquisitionLane.CT)
        if ct_plan is None or not ct_plan.enabled:
            self._nonfeed_predispatch_done = True
            return
        if not is_lane_enabled(self._acquisition_plan, AcquisitionLane.CT):
            self._nonfeed_predispatch_done = True
            return
        _ct_already_run = (
            self._result.ct_log_discovered > 0
            or self._result.lane_ct_accepted_findings > 0
            or getattr(self._ct_log_client, "_called", False)
        )
        if _ct_already_run:
            self._nonfeed_predispatch_done = True
            return
        self._result.windup_blocked_until_nonfeed_attempted = True
        log.debug("[F207M-A] Nonfeed pre-dispatch: blocking windup until CT attempted")
        _uma = "ok"
        if self._governor is not None:
            try:
                _snap = await self._governor.evaluate()
                _uma = getattr(_snap, "uma_state", "ok")
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        _memory_ok = _uma in ("ok", "warn")
        from hledac.universal.runtime.acquisition_strategy import AcquisitionLaneOutcome

        _t0 = _time.monotonic()
        _skipped: dict[str, str] = {}
        _attempted_lanes: list[str] = []
        _outcome: AcquisitionLaneOutcome | None = None

        async def _run_ct_predispatch() -> AcquisitionLaneOutcome:
            """CT pre-dispatch with max_items=5, timeout=15s."""
            _start = _time.monotonic()
            _candidate_findings: tuple = ()
            _rejection_reasons: tuple = ()
            _rejected_count = 0
            _sample_rejections: tuple = ()
            _ct_error: str | None = None
            _ct_results_raw = 0
            _candidates: tuple = ()
            try:
                async with asyncio.timeout(15.0):
                    _ct_call = _get_ct_adapter()
                    from hledac.universal.runtime.acquisition_strategy import build_lane_query

                    _shaped = build_lane_query(query, AcquisitionLane.CT)
                    if isinstance(_shaped, dict) or not _shaped:
                        raise ValueError("empty_ct_query")
                    result, ct_outcome = await _ct_call(query=_shaped, max_results=5, timeout_s=15.0)
                    _ct_results_raw = ct_outcome.raw_count
                    candidates, rejections, _ct_telemetry = ct_results_to_findings(
                        result, ct_outcome, query, sprint_id=f"predispatch-{int(_time.time())}"
                    )
                    _candidate_findings = tuple(candidates)
                    _rejection_reasons = tuple(rejections)
                    _rejected_count = len(rejections)
                    _sample_rejections = tuple(rejections[:3])
                    accepted = 0
                    if _candidate_findings and duckdb_store is not None:
                        if self._duckdb_can_ingest:
                            try:
                                ingest_results = await self._gate_then_ingest_and_accumulate(
                                    duckdb_store, list(_candidate_findings)
                                )
                                if self._evidence_log is not None:
                                    try:
                                        self._evidence_log.create_event(
                                            "decision",
                                            {
                                                "gate": "ingest",
                                                "findings_count": len(_candidate_findings),
                                                "accepted": True,
                                            },
                                            source_ids=[],
                                            confidence=1.0,
                                        )
                                    except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                        pass
                                accepted = sum((1 for r in ingest_results if isinstance(r, dict) and r.get("accepted")))
                            except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                log.warning(
                                    "sprint %s: quality gate ingest failed -- %s: %s",
                                    getattr(self._result, "sprint_id", "?"),
                                    type(_exc).__name__,
                                    _exc,
                                )
                                ingest_results = []
                            else:
                                record_ct_storage_results(_ct_telemetry, ingest_results)
                    if ct_outcome.error:
                        _ct_error = ct_outcome.error
                    _bridge_raw = _ct_telemetry.get("ct_raw_entries", 0) if isinstance(_ct_telemetry, dict) else 0
                    _bridge_candidates = len(candidates) if candidates else 0
                    _raw_keys: tuple[str, ...] = ()
                    if _bridge_raw > 0 and hasattr(result, "hits") and result.hits:
                        _sample_hits = list(result.hits)[:3]
                        _raw_keys = tuple(
                            sorted(
                                {
                                    k
                                    for hit in _sample_hits
                                    for k in (getattr(hit, "url", None), getattr(hit, "ct_name_value", None))
                                    if k
                                }
                            )
                        )
                    _storage_attempted = (
                        bool(_candidate_findings) and duckdb_store is not None and self._duckdb_can_ingest
                    )
                    _ct_cache_used = getattr(ct_outcome, "ct_cache_used", False)
                    if _ct_cache_used:
                        _ct_loss_stage = CTLossStage.STALE_CACHE_USED.value
                        self._result.ct_bridge_invoked = False
                    elif _ct_results_raw == 0:
                        _ct_loss_stage = CTLossStage.PROVIDER_FAILURE.value
                        self._result.ct_bridge_invoked = False
                    elif (
                        _bridge_raw == 0
                        and _ct_results_raw > 0
                        and (_bridge_candidates == 0)
                        and (REJECTION_UNSUPPORTED_SHAPE in _rejection_reasons)
                    ):
                        _ct_loss_stage = CTLossStage.UNSUPPORTED_RAW_SHAPE.value
                        self._result.ct_bridge_invoked = True
                    elif _bridge_candidates == 0 and _bridge_raw > 0:
                        _ct_loss_stage = CTLossStage.ALL_REJECTED_BY_BRIDGE.value
                        self._result.ct_bridge_invoked = True
                    elif _bridge_candidates > 0 and (not _storage_attempted):
                        _ct_loss_stage = CTLossStage.CANDIDATES_BUILT_NOT_ACCUMULATED.value
                        self._result.ct_bridge_invoked = True
                    elif _bridge_candidates > 0 and accepted == 0:
                        _ct_loss_stage = CTLossStage.ACCUMULATED_NOT_STORED.value
                        self._result.ct_bridge_invoked = True
                    elif _bridge_candidates > 0 and accepted > 0:
                        _ct_loss_stage = CTLossStage.NO_LOSS.value
                        self._result.ct_bridge_invoked = True
                    elif _ct_results_raw > 0 and _bridge_candidates == 0 and (_bridge_raw == 0):
                        _ct_loss_stage = CTLossStage.UNKNOWN_LOSS.value
                        self._result.ct_bridge_invoked = True
                    else:
                        _ct_loss_stage = CTLossStage.RAW_NOT_BRIDGED.value
                        self._result.ct_bridge_invoked = False
                    self._result.ct_loss_stage = _ct_loss_stage
                    _ct_raw = _ct_results_raw
                    if _ct_raw > 0:
                        self._emit_source_family_event(family="CT", event="raw_received", count=_ct_raw)
                    self._emit_source_family_event(
                        family="CT", event="terminal", reason="bridge_invoked", terminal_state="success"
                    )
                    self._result.ct_raw_sample_keys = _raw_keys
                    self._result.ct_raw_sample_count = _bridge_raw
                    self._result.ct_raw_count = _ct_results_raw
                    self._result.ct_candidates_built = _bridge_candidates
                    self._result.ct_bridge_rejections_count = _rejected_count
                    self._result.ct_bridge_rejection_reasons = tuple((str(r) for r in _sample_rejections))
                    self._result.ct_candidates_accumulated = _bridge_candidates
                    self._result.ct_candidates_stored = accepted
                    self._result.ct_storage_rejected = max(0, _bridge_candidates - accepted)
                    if isinstance(_ct_telemetry, dict):
                        self._result.ct_candidate_count = _ct_telemetry.get("ct_bridge_candidate_count", 0)
                        self._result.ct_valid_domain_count = _ct_telemetry.get("ct_bridge_valid_domain_count", 0)
                        self._result.ct_bridge_build_success_count = _ct_telemetry.get(
                            "ct_bridge_build_success_count", 0
                        )
                        self._result.ct_bridge_quality_rejected_count = _ct_telemetry.get(
                            "ct_bridge_quality_rejected_count", 0
                        )
                        self._result.ct_raw_domains_seen = _ct_telemetry.get("ct_raw_domains_seen", 0)
                        self._result.ct_unique_domains_seen = _ct_telemetry.get("ct_unique_domains_seen", 0)
                        self._result.ct_valid_public_domains = _ct_telemetry.get("ct_valid_public_domains", 0)
                        self._result.ct_wildcard_domains = _ct_telemetry.get("ct_wildcard_domains", 0)
                        self._result.ct_private_reserved_domains = _ct_telemetry.get("ct_private_reserved_domains", 0)
                        self._result.ct_duplicate_candidates = _ct_telemetry.get("ct_duplicate_candidates", 0)
                        self._result.ct_expansion_clues_count = _ct_telemetry.get("ct_expansion_clues_count", 0)
                        _ct_examples = _ct_telemetry.get("ct_candidate_examples", []) or []
                        if _ct_examples:
                            _ex_samples: list[str] = []
                            for _ex in _ct_examples[:5]:
                                try:
                                    _ex_samples.append(_msgspec_encode(_ex).decode())
                                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                    pass
                            self._result.ct_candidate_examples = tuple(_ex_samples)
                    _ct_quarantine_count = (
                        _ct_telemetry.get("ct_quarantine_count", 0) if isinstance(_ct_telemetry, dict) else 0
                    )
                    _ct_quarantine_entries = (
                        _ct_telemetry.get("ct_quarantine_entries", []) if isinstance(_ct_telemetry, dict) else []
                    )
                    self._result.ct_quarantine_count = _ct_quarantine_count
                    if _ct_quarantine_entries:
                        _samples: list[str] = []
                        for _entry in _ct_quarantine_entries[:10]:
                            try:
                                _samples.append(_msgspec_encode(_entry).decode())
                            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                pass
                        self._result.ct_quarantine_samples = tuple(_samples)
                    for _entry in _ct_quarantine_entries:
                        try:
                            self._nonfeed_ledger.add_ct_quarantine(
                                domain=_entry.get("raw_value", ""),
                                reject_reason=_entry.get("reject_reason", "unknown"),
                                source_url=_entry.get("source_url", ""),
                                query=_entry.get("normalized_query", ""),
                            )
                        except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                            pass
                    self._result.ct_provider_status = str(getattr(ct_outcome, "provider_status", "") or "")
                    self._result.ct_cache_used = getattr(ct_outcome, "ct_cache_used", False)
                    self._result.ct_cache_stale = getattr(ct_outcome, "ct_cache_stale", False)
                    self._result.ct_cache_age_s = getattr(ct_outcome, "ct_cache_age_s", 0.0)
                    return AcquisitionLaneOutcome(
                        lane=AcquisitionLane.CT,
                        enabled=True,
                        attempted=True,
                        accepted_findings=accepted,
                        produced_items=_ct_results_raw,
                        duration_s=_time.monotonic() - _start,
                        source_family="ct",
                        ct_query=_shaped,
                        ct_results_raw=_ct_results_raw,
                        error=_ct_error,
                        candidate_findings=_candidate_findings,
                        rejection_reasons=_rejection_reasons,
                        rejected_count=_rejected_count,
                        sample_rejections=_sample_rejections,
                    )
            except TimeoutError:
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.CT,
                    enabled=True,
                    attempted=True,
                    timeout=True,
                    duration_s=_time.monotonic() - _start,
                    error="predispatch_timeout",
                    source_family="ct",
                    ct_query=str(_shaped) if "_shaped" in dir() else "",
                    ct_results_raw=_ct_results_raw,
                    candidate_findings=_candidate_findings,
                    rejection_reasons=_rejection_reasons,
                    rejected_count=_rejected_count,
                    sample_rejections=_sample_rejections,
                )
            except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.CT,
                    enabled=True,
                    attempted=True,
                    error=f"predispatch_error:{type(exc).__name__}:{exc}",
                    duration_s=_time.monotonic() - _start,
                    source_family="ct",
                    ct_query=str(_shaped) if "_shaped" in dir() else "",
                    ct_results_raw=_ct_results_raw,
                    candidate_findings=_candidate_findings,
                    rejection_reasons=_rejection_reasons,
                    rejected_count=_rejected_count,
                    sample_rejections=_sample_rejections,
                )

        try:
            _outcome = await _run_ct_predispatch()
            _attempted_lanes.append("ct")
        except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F207M-A] CT pre-dispatch failed: %s", exc)
            _skipped["ct"] = f"predispatch_exception:{type(exc).__name__}"
        if _memory_ok and _outcome and (not _outcome.timeout):
            from hledac.universal.runtime.acquisition_strategy import build_lane_query

            _wayback_shaped = build_lane_query(query, AcquisitionLane.WAYBACK)
            if _wayback_shaped and (not isinstance(_wayback_shaped, dict)):
                try:
                    from hledac.universal.intelligence.wayback_diff_miner import WaybackDiffMiner

                    miner = WaybackDiffMiner()
                    try:
                        result = await miner.mine([str(_wayback_shaped)])
                    finally:
                        await miner.close()
                    candidates, rejections, wayback_telemetry = wayback_results_to_findings(
                        result, query, sprint_id=f"predispatch-wb-{int(_time.time())}"
                    )
                    if wayback_telemetry:
                        self._result.wayback_advisory_clues_count += wayback_telemetry.get("wayback_changed_count", 0)
                        self._result.wayback_changed_url_count += wayback_telemetry.get("wayback_changed_url_count", 0)
                        self._result.wayback_added_url_count += wayback_telemetry.get("wayback_added_count", 0)
                        self._result.wayback_digest_changed_count += wayback_telemetry.get(
                            "wayback_digest_changed_count", 0
                        )
                        self._result.wayback_unchanged_rejected += wayback_telemetry.get(
                            "wayback_unchanged_rejected", 0
                        )
                    _attempted_lanes.append("wayback")
                except Exception as exc:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
                    _skipped["wayback"] = f"{type(exc).__name__}:{exc}"
            else:
                _skipped["wayback"] = "empty_query_or_disabled"
        else:
            _skip_reason = (
                "memory_critical" if not _memory_ok else "ct_timeout" if _outcome and _outcome.timeout else "ct_failed"
            )
            _skipped["wayback"] = _skip_reason
        if _memory_ok:
            from hledac.universal.runtime.acquisition_strategy import build_lane_query

            _pdns_shaped = build_lane_query(query, AcquisitionLane.PASSIVE_DNS)
            if _pdns_shaped and (not isinstance(_pdns_shaped, dict)):
                try:
                    from hledac.universal.security.passive_dns import call_lookup_passive_dns

                    ips, pdns_outcome = await call_lookup_passive_dns(str(_pdns_shaped))
                    pdns_findings, pdns_rejections, pdns_telemetry = passive_dns_results_to_findings(
                        ips, pdns_outcome, query, sprint_id=f"predispatch-pdns-{int(_time.time())}"
                    )
                    if pdns_telemetry:
                        self._result.passive_dns_advisory_clues_count += pdns_telemetry.get("pdns_public_accepted", 0)
                        self._result.passive_dns_private_ip_rejected += pdns_telemetry.get("pdns_private_rejected", 0)
                        self._result.passive_dns_empty_ip_rejected += pdns_telemetry.get("pdns_empty_rejected", 0)
                    _attempted_lanes.append("passive_dns")
                except Exception as exc:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
                    _skipped["passive_dns"] = f"{type(exc).__name__}:{exc}"
            else:
                _skipped["passive_dns"] = "empty_query_or_disabled"
        else:
            _skipped["passive_dns"] = "memory_critical"
        _duration = _time.monotonic() - _t0
        self._result.nonfeed_predispatch_attempted = True
        self._result.nonfeed_predispatch_lanes = tuple(_attempted_lanes)
        self._result.nonfeed_predispatch_skipped = dict(_skipped)
        self._result.nonfeed_predispatch_duration_s = _duration
        if _outcome is not None:
            _outcomes = (_outcome,)
            self._lane_outcomes = _outcomes
            self._result.acquisition_lane_outcomes = _outcomes
            self._accumulate_lane_findings(_outcomes, query)
        self._nonfeed_predispatch_done = True
        log.debug(
            "[F207M-A] Nonfeed pre-dispatch done: lanes=%s, skipped=%s, dur=%.2fs",
            _attempted_lanes,
            _skipped,
            _duration,
        )

    async def _required_pre_windup_lanes(self, query: str, acquisition_plan: Any, memory_state: str) -> tuple[str, ...]:
        """

        Sprint F208B: Determine required lanes before windup.



        Delegates to required_terminal_lanes() from acquisition_strategy,

        which owns the canonical terminality policy (not the scheduler).



        Returns tuple of required lane names (lowercase).

        """
        if acquisition_plan is None:
            return ()
        uma_state = memory_state
        swap_detected = False
        if self._governor is not None:
            try:
                _snap = await self._governor.evaluate()
                uma_state = getattr(_snap, "uma_state", memory_state)
                swap_detected = getattr(_snap, "swap_detected", False)
            except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
                pass
        mlt_tuples = required_terminal_lanes(
            snapshot=acquisition_plan, query=query, uma_state=uma_state, swap_detected=swap_detected
        )
        return tuple((mlt.lane.lower() for mlt in mlt_tuples))

    async def _ensure_pre_windup_lane_terminal_states(
        self, query: str, acquisition_plan: Any, memory_state: str
    ) -> PreWindupBarrierResult:
        """

        Sprint F207Q-A: Ensure required lanes have terminal state before windup.



        This is the hard pre-windup barrier -- it attempts required cheap lanes

        (PUBLIC, CT) if they have not yet reached terminal state.



        Invariants:

          - Never calls stealth lane

          - Never directly writes DB or graph

          - Uses existing lane runner / adapter indirection

          - Bounded timeout per lane (max 15s per lane)

          - Fail-soft: adapter error becomes terminal error, not crash

          - Records all telemetry on self._result



        Args:

            query: Sprint query

            acquisition_plan: Acquisition plan from build_acquisition_plan

            memory_state: "ok" | "warn" | "critical" | "emergency"



        Returns:

            PreWindupBarrierResult describing what happened

        """
        import time as _time

        required = await self._required_pre_windup_lanes(query, acquisition_plan, memory_state)
        self._result.prewindup_barrier_checked = True
        self._result.prewindup_barrier_required_lanes = required
        if not required:
            self._result.prewindup_barrier_satisfied = True
            self._result.prewindup_barrier_attempted_lanes = ()
            self._result.prewindup_barrier_skipped_lanes = {}
            self._result.prewindup_barrier_errors = {}
            self._result.prewindup_barrier_duration_s = 0.0
            return PreWindupBarrierResult(satisfied=True, required_lanes=())
        t0 = _time.monotonic()
        attempted: list[str] = []
        skipped: dict[str, str] = {}
        errors: dict[str, str] = {}
        _ct_done = (
            self._result.ct_log_discovered > 0
            or self._result.lane_ct_accepted_findings > 0
            or self._result.ct_request_timeout
            or (
                self._result.ct_terminal_stage
                in ("error", "skipped", "request_timeout", "no_candidates", "provider_cooldown", "provider_unavailable")
            )
        )
        _public_done = self._public_outcome is not None
        _tasks: dict[str, asyncio.Task] = {}
        for lane in required:
            if lane == "public" and _public_done:
                _pub_timeout = self._public_outcome.get("timeout", False) if self._public_outcome else False
                _pub_error = self._public_outcome.get("error", None) if self._public_outcome else None
                if _pub_timeout:
                    skipped["public"] = "terminal_by_timeout"
                elif _pub_error:
                    skipped["public"] = "terminal_by_error"
                else:
                    skipped["public"] = "terminal_by_outcome"
                continue
            if lane == "ct" and _ct_done:
                _ct_timeout = self._result.ct_request_timeout
                _ct_error = self._result.ct_terminal_stage in ("error", "skipped")
                if _ct_timeout:
                    skipped["ct"] = "terminal_by_timeout"
                elif _ct_error:
                    skipped["ct"] = "terminal_by_error"
                else:
                    skipped["ct"] = "terminal_by_outcome"
                continue
            if lane == "public":
                _tasks["public"] = safe_create_task(
                    self._attempt_public_prewindup_barrier(query), name="prewindup:public"
                )
            elif lane == "ct":
                _tasks["ct"] = safe_create_task(self._attempt_ct_prewindup_barrier(query), name="prewindup:ct")
        if _tasks:
            from hledac.universal.utils.async_helpers import safe_gather

            _result = await safe_gather(*_tasks.values(), label="prewindup_barriers")
            for lane_id, outcome in zip(_tasks.keys(), _result.ok):
                if outcome is None:
                    skipped[lane_id] = "adapter_error"
                    errors[lane_id] = f"prewindup_barrier_{lane_id}_error"
                elif isinstance(outcome, Exception):
                    skipped[lane_id] = "exception"
                    errors[lane_id] = f"{type(outcome).__name__}:{outcome}"
                elif outcome.get("error"):
                    errors[lane_id] = outcome["error"]
                    attempted.append(lane_id)
                elif outcome.get("timeout"):
                    skipped[lane_id] = "timeout"
                    attempted.append(lane_id)
                else:
                    attempted.append(lane_id)
        duration = _time.monotonic() - t0
        _barrier_scope = {"public", "ct"}
        _handled = {r for r in required if r in _barrier_scope}
        satisfied = (
            len(attempted) >= len(_handled) or all((r in skipped or r in attempted for r in _handled))
        ) and all((r in skipped or r in attempted for r in _handled))
        self._result.prewindup_barrier_checked = True
        self._result.prewindup_barrier_required_lanes = required
        self._result.prewindup_barrier_satisfied = satisfied
        self._result.prewindup_barrier_attempted_lanes = tuple(attempted)
        self._result.prewindup_barrier_skipped_lanes = skipped
        self._result.prewindup_barrier_errors = errors
        self._result.prewindup_barrier_duration_s = duration
        return PreWindupBarrierResult(
            required_lanes=required,
            satisfied=satisfied,
            attempted_lanes=tuple(attempted),
            skipped_lanes=tuple(skipped.keys()),
            error_lanes=tuple(errors.keys()),
            duration_s=duration,
        )

    async def _attempt_public_prewindup_barrier(self, query: str) -> dict | None:
        """

        Sprint F207Q-A: Attempt PUBLIC lane as part of pre-windup barrier.



        Args:

            query: Sprint query for lane query shaping.



        Returns dict with keys: attempted, error, timeout, or None on exception.

        Uses tiny bounds (max 3 results, 10s timeout).

        """
        try:
            from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
            from hledac.universal.runtime.acquisition_strategy import AcquisitionLane, build_lane_query

            shaped = build_lane_query(query, AcquisitionLane.PUBLIC)
            if isinstance(shaped, dict) or not shaped:
                return {"error": "empty_public_query"}
            try:
                async with asyncio.timeout(10.0):
                    result = await async_run_live_public_pipeline(
                        query=shaped,
                        store=None,
                        max_results=3,
                        fetch_timeout_s=10.0,
                        fetch_concurrency=2,
                        hermes_engine=None,
                        memory_manager=None,
                        enqueue_hypothesis_pivot=None,
                    )
                return {"attempted": True, "accepted": getattr(result, "accepted_findings", 0)}
            except TimeoutError:
                return {"attempted": True, "timeout": True}
        except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return {"attempted": False, "error": f"{type(exc).__name__}:{exc}"}

    async def _attempt_ct_prewindup_barrier(self, query: str) -> dict | None:
        """

        Sprint F207Q-A: Attempt CT lane as part of pre-windup barrier.



        Args:

            query: Sprint query for lane query shaping.



        Returns dict with keys: attempted, error, timeout, or None on exception.

        Uses tiny bounds (max 5 results, 15s timeout).

        """
        try:
            from hledac.universal.runtime.acquisition_strategy import AcquisitionLane, build_lane_query

            shaped = build_lane_query(query, AcquisitionLane.CT)
            if isinstance(shaped, dict) or not shaped:
                return {"error": "empty_ct_query"}
            try:
                _ct_domains = {"crt.sh", "crt.sh.identity", "api.certspotter.com"}
                _cb_states = get_all_breaker_states()
                for _d in _ct_domains:
                    if _cb_states.get(_d) in ("open", "half_open"):
                        _breaker = _BREAKERS.get(_d)
                        if _breaker is not None:
                            _breaker._state = CBState.CLOSED
                            _breaker._failure_count = 0
                            _breaker._consecutive_timeouts = 0
                            logger.debug(f"[GAP-3/1] CT breaker reset for: {_d}")
            except Exception:  # noqa: BLE001 — best-effort; logging failure; non-critical
                pass
            _ct_call = _get_ct_adapter()
            try:
                async with asyncio.timeout(15.0):
                    ct_result, ct_outcome = await _ct_call(query=shaped, max_results=5, timeout_s=15.0)
                return {"attempted": True, "raw_count": getattr(ct_outcome, "raw_count", 0)}
            except TimeoutError:
                return {"attempted": True, "timeout": True}
        except Exception as exc:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            return {"attempted": False, "error": f"{type(exc).__name__}:{exc}"}

    async def _run_feed_dominance_nonfeed_rescue_window(self, query: str, duckdb_store: Any) -> float | None:
        """

        Sprint F220D: Feed Dominance Nonfeed Rescue Window.



        When feed has been dominant (>=1000 accepted) and nonfeed lanes are all

        at zero, this rescue window attempts a final bounded nonfeed rescue before

        declaring feed-only early exit.



        Bounded:

          - Max 60s wall-clock duration

          - Fail-soft: returns None on any error, 0.0 if no candidates found

          - No new network providers -- uses existing seams (_attempt_public_prewindup_barrier)

          - No MLX / browser / stealth



        Returns:

            Elapsed seconds if rescue ran (even with 0 findings), None if skipped.

        """
        import time as _time

        _t0 = _time.monotonic()
        _hard_deadline = _t0 + 60.0
        try:
            if self._hard_deadline_monotonic is not None:
                _remaining = self._hard_deadline_monotonic - _time.monotonic()
                if _remaining <= 0:
                    log.debug("[F220D] Rescue window skipped: hard deadline already exceeded")
                    return None
                if _remaining < 15.0:
                    log.debug(f"[F220D] Rescue window skipped: only {_remaining:.1f}s remaining")
                    return None
            _rescue_findings = 0
            try:
                _findings = await duckdb_store.async_get_recent_findings(limit=1000)
                if not _findings:
                    self._result.pivot_integration_reason = "pivot_no_findings"
                else:
                    _texts = [f.payload_text for f in _findings if getattr(f, "payload_text", None)]
                    if not _texts:
                        self._result.pivot_integration_reason = "pivot_no_payload_text"
                    else:
                        _extraction = extract_pivot_seeds_from_texts(
                            _texts, source_family="feed", max_texts=1000, max_seeds=256
                        )
                        _seed_count = len(_extraction.seeds)
                        self._result.pivot_seed_count = _seed_count
                        _type_counts: dict[str, int] = {}
                        for _s in _extraction.seeds:
                            _t = getattr(_s, "seed_type", "") or ""
                            _type_counts[_t] = _type_counts.get(_t, 0) + 1
                        self._result.pivot_seed_type_counts = _type_counts
                        _sample = tuple(sorted({getattr(_s, "value", "") or "" for _s in _extraction.seeds})[:10])
                        self._result.pivot_seed_sample = _sample
                        if _seed_count == 0:
                            self._result.pivot_integration_reason = "pivot_no_seeds"
                        else:
                            _domains = tuple(sorted({s.value for s in _extraction.seeds if s.seed_type == "domain"}))
                            _ips = tuple(sorted({s.value for s in _extraction.seeds if s.seed_type in ("ip", "ipv4")}))
                            _urls = tuple(sorted({s.value for s in _extraction.seeds if s.seed_type == "url"}))
                            _hashes = tuple(sorted({s.value for s in _extraction.seeds if s.seed_type == "hash"}))
                            _cves = tuple(sorted({s.value for s in _extraction.seeds if s.seed_type == "cve"}))
                            self._result.pivot_seed_domains = _domains
                            self._result.pivot_seed_ips = _ips
                            self._result.pivot_seed_urls = _urls
                            self._result.pivot_seed_hashes = _hashes
                            self._result.pivot_seed_cves = _cves
                            _plan = plan_lanes_for_pivot_seeds(_extraction.seeds, max_items=128)
                            self._result.pivot_lane_plan_count = len(_plan.items)
                            self._result.planned_pivot_lanes = tuple(
                                sorted({item.lane.upper() for item in _plan.items})
                            )
                            self._result.pivot_integration_reason = (
                                "pivot_planned" if _plan.items else "pivot_planner_empty"
                            )
            except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.debug(f"[F220G] Pivot extraction error: {_exc}")
                self._result.pivot_integration_reason = "pivot_error"
            _tasks: dict[str, asyncio.Task] = {}
            _tasks["public"] = safe_create_task(self._attempt_public_prewindup_barrier(query), name="rescue:public")
            if _time.monotonic() < _hard_deadline:
                _tasks["ct"] = safe_create_task(self._attempt_ct_prewindup_barrier(query), name="rescue:ct")
            if _tasks:
                from hledac.universal.utils.async_helpers import safe_gather

                _results = await safe_gather(*_tasks.values(), label="rescue_barriers")
                for _lane_id, _outcome in zip(_tasks.keys(), _results.ok):
                    if _lane_id == "public" and isinstance(_outcome, dict):
                        if _outcome.get("accepted", 0) > 0:
                            _rescue_findings += _outcome["accepted"]
                            log.debug(f"[F220D] PUBLIC rescue: {_outcome['accepted']} accepted")
                    elif _lane_id == "ct" and isinstance(_outcome, dict):
                        if _outcome.get("raw_count", 0) > 0:
                            log.debug(f"[F220D] CT rescue: {_outcome['raw_count']} raw results")
                for _lane_id, _outcome in zip(_tasks.keys(), _results.errors):
                    if isinstance(_outcome, TimeoutError):
                        log.debug(f"[F220D] {_lane_id.upper()} rescue timed out (15s)")
                    elif isinstance(_outcome, Exception):
                        log.debug(f"[F220D] {_lane_id.upper()} rescue error: {_outcome}")
            _elapsed = _time.monotonic() - _t0
            log.debug(f"[F220D] Rescue window completed in {_elapsed:.1f}s, findings={_rescue_findings}")
            return _elapsed
        except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug(f"[F220D] Rescue window exception: {_exc}")
            return None

    async def _run_mandatory_acquisition_prelude(
        self, result: SprintSchedulerResult, query: str, duckdb_store: Any, ct_log_client: Any
    ) -> None:
        """

        Sprint F209A: Mandatory Acquisition Prelude.



        Runs BEFORE the main feed cycle loop to establish early terminal state for

        PUBLIC and CT lanes on domain queries.



        This fixes the active300 window issue where windup_lead_s=180s means the

        feed loop dominates before PUBLIC/CT can get a reliable execution slot.



        Rules:

          - PUBLIC and CT required for domain queries (per required_terminal_lanes)

          - Each lane gets ONE bounded attempt before cycles_started is incremented

          - PUBLIC: max 3 results, 10s timeout via live_public_pipeline

          - CT: max 5 results, 15s timeout via crtsh adapter

          - CancelledError is re-raised (prelude is mandatory for domain queries)

          - Skipped/error outcomes are recorded as terminal state

          - No stealth, no browser, no MLX load



        Integration:

          - _public_outcome dict (same shape as _attempt_public_prewindup_barrier)

          - _lane_outcomes tuple with AcquisitionLaneOutcome (same as predispatch)

          - _result.acquisition_lane_outcomes (SSOT for terminality_report)

          - After prelude, _finalize_result_truth captures initial terminality

        """
        import time as _time

        _t0 = _time.monotonic()
        self._result.acquisition_prelude_checked = True
        _nd_debug = getattr(self._acquisition_plan, "nonfeed_plan_debug", None) if self._acquisition_plan else None
        _is_nonfeed_diagnostic = getattr(_nd_debug, "is_nonfeed_diagnostic", False) if _nd_debug else False
        if not _is_nonfeed_diagnostic and self._config.acquisition_profile == "nonfeed_diagnostic":
            _is_nonfeed_diagnostic = True
        _is_deep_osint_m1 = False
        if _nd_debug:
            _profile = getattr(_nd_debug, "acquisition_profile", "") or ""
            _is_deep_osint_m1 = _profile == "deep_osint_m1"
        if not _is_deep_osint_m1 and self._config.acquisition_profile == "deep_osint_m1":
            _is_deep_osint_m1 = True
        _uma = "ok"
        if self._governor is not None:
            try:
                _snap = await self._governor.evaluate()
                _uma = getattr(_snap, "uma_state", "ok")
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        _nonfeed_prelude_done = False
        _nonfeed_prelude_accepted: dict[str, int] = {}
        _nonfeed_prelude_skipped: dict[str, str] = {}
        _nonfeed_prelude_errors: dict[str, str] = {}
        _nonfeed_prelude_attempted: list[str] = []
        _nonfeed_prelude_terminal: list[str] = []
        _nonfeed_prelude_expected: list[str] = []
        _has_domain = False
        _domain_error = ""
        try:
            from hledac.universal.runtime.acquisition_strategy import _has_domain_or_ip

            _has_domain = _has_domain_or_ip(query)
        except Exception as _exc:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            _domain_error = f"{type(_exc).__name__}:{_exc}"
            _has_domain = False
        self._result.acquisition_prelude_domain_detected = _has_domain
        self._result.acquisition_prelude_plan_present = self._acquisition_plan is not None
        self._result.acquisition_prelude_domain_detection_error = _domain_error
        if not _has_domain and (not _is_nonfeed_diagnostic):
            self._result.acquisition_prelude_ran = False
            if _domain_error:
                self._result.acquisition_prelude_reason = f"domain_detection_error:{_domain_error}"
            else:
                self._result.acquisition_prelude_reason = "non_domain_query"
            self._result.acquisition_prelude_required_lanes = ()
            self._result.acquisition_prelude_duration_s = _time.monotonic() - _t0
            return
        _required: tuple[str, ...] = ()
        _mlt_tuples: tuple = ()
        _plan_built = False
        _rtl = None
        if self._acquisition_plan is not None:
            try:
                from hledac.universal.runtime.acquisition_strategy import required_terminal_lanes as _rtl

                _mlt_tuples = _rtl(snapshot=self._acquisition_plan, query=query, uma_state=_uma, swap_detected=False)
                _required: tuple[str, ...] = cast(
                    tuple[str, ...],
                    tuple(
                        (
                            mlt.lane.value if hasattr(mlt.lane, "value") else str(mlt.lane).upper()
                            for mlt in _mlt_tuples
                            if getattr(mlt, "required", False)
                        )
                    ),
                )
            except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.warning("required_terminal_lanes failed, using default: %s", _exc)
                _required = ("PUBLIC", "CT")
        else:
            try:
                from hledac.universal.runtime.acquisition_strategy import build_acquisition_plan
                from hledac.universal.runtime.acquisition_strategy import required_terminal_lanes as _rtl

                _minimal = build_acquisition_plan(
                    query=query,
                    duration_s=60.0,
                    aggressive_mode=self._config.aggressive_mode,
                    uma_state=_uma,
                    swap_detected=False,
                    acquisition_profile=self._config.acquisition_profile
                    if self._config.acquisition_profile is not None
                    else "default",
                    source_quality_weights=self._policy_manager.get_src_quality_weights()
                    if self._policy_manager is not None and self._policy_manager.enabled
                    else None,
                )
                if _minimal is not None:
                    self._acquisition_plan = _minimal
                    _plan_built = True
                    _mlt_tuples = _rtl(snapshot=_minimal, query=query, uma_state=_uma, swap_detected=False)
                    _required: tuple[str, ...] = cast(
                        tuple[str, ...],
                        tuple(
                            (
                                mlt.lane.value if hasattr(mlt.lane, "value") else str(mlt.lane).upper()
                                for mlt in _mlt_tuples
                                if getattr(mlt, "required", False)
                            )
                        ),
                    )
                else:
                    _required = ("PUBLIC", "CT")
            except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.debug("Minimal plan build failed, using PUBLIC/CT: %s", _exc)
                _required = ("PUBLIC", "CT")
                self._result.acquisition_plan_build_error_for_prelude = str(_exc)[:200]
        self._result.acquisition_plan_present_for_prelude = self._acquisition_plan is not None
        _all_lanes: list[str] = []
        _enabled_lanes: list[str] = []
        if self._acquisition_plan is not None:
            for _plan in getattr(self._acquisition_plan, "plans", []):
                _ln = getattr(_plan.lane, "value", str(_plan.lane))
                _all_lanes.append(_ln)
                if getattr(_plan, "enabled", False):
                    _enabled_lanes.append(_ln)
            _nd = getattr(self._acquisition_plan, "nonfeed_plan_debug", None)
            self._result.acquisition_plan_profile_for_prelude = getattr(_nd, "acquisition_profile", "") if _nd else ""
        self._result.acquisition_plan_lanes_for_prelude = tuple(_all_lanes)
        self._result.acquisition_plan_enabled_lanes_for_prelude = tuple(_enabled_lanes)
        self._result.acquisition_prelude_plan_built_for_prelude = _plan_built
        _needs_public = "PUBLIC" in _required or "public" in [r.lower() for r in _required]
        _needs_ct = "CT" in _required or "ct" in [r.lower() for r in _required]
        self._result.acquisition_prelude_required_lanes = _required
        self._result.acquisition_prelude_reason = "domain_query_requires_prelude"
        _attempted_lanes: list[str] = []
        _terminal_lanes: list[str] = []
        _skipped: dict[str, str] = {}
        _errors: dict[str, str] = {}
        _prelude_outcomes: list = []
        _seed_ctx: Any = None
        _public_task: asyncio.Task | None = None
        _ct_task: asyncio.Task | None = None
        _coros_for_gather: list = []
        if _needs_public:
            _coros_for_gather.append(self._run_public_prelude_lane(query))
        if _needs_ct:
            _coros_for_gather.append(self._run_ct_prelude_lane(query, _seed_ctx))
        _ok_results: list
        _gathered_errors: list
        if _coros_for_gather:
            _parallel_result = await parallel(
                _coros_for_gather, concurrency=2, ctx="prelude_gather"
            )
            _ok_results, _gathered_errors = _parallel_result.ok, list(_parallel_result.errors)
        else:
            _ok_results, _gathered_errors = ([], [])
        _public_result_from_gather: dict | None = None
        _ct_outcome_from_gather: Any = None
        _ct_result_from_gather: Any = None
        _ct_telemetry_from_gather: Any = None
        _result_idx = 0
        if _needs_public:
            if _gathered_errors and _result_idx < len(_gathered_errors):
                _exc = _gathered_errors[_result_idx]
                _errors["PUBLIC"] = f"{type(_exc).__name__}:{_exc}"
                _skipped["PUBLIC"] = f"prelude_error:{type(_exc).__name__}"
            else:
                _public_result_from_gather = _ok_results[_result_idx] if _result_idx < len(_ok_results) else None
            _result_idx += 1
        if _needs_ct:
            if _gathered_errors and _result_idx < len(_gathered_errors):
                _exc = _gathered_errors[_result_idx]
                _errors["CT"] = f"{type(_exc).__name__}:{_exc}"
                _skipped["CT"] = f"prelude_error:{type(_exc).__name__}"
            else:
                _ct_result_tuple = _ok_results[_result_idx] if _result_idx < len(_ok_results) else None
                if _ct_result_tuple is not None:
                    _ct_outcome_from_gather, _ct_result_from_gather, _ct_telemetry_from_gather = _ct_result_tuple
            _result_idx += 1
        if _public_result_from_gather is not None:
            _public_result = _public_result_from_gather
            if _public_result.get("attempted"):
                _attempted_lanes.append("PUBLIC")
                _terminal_lanes.append("PUBLIC")
            if _public_result.get("skip_reason"):
                _skipped["PUBLIC"] = _public_result["skip_reason"]
            self._public_outcome = _public_result
        if _ct_outcome_from_gather is not None:
            _ct_outcome_prelude = _ct_outcome_from_gather
            _prelude_outcomes.append(_ct_outcome_prelude)
            self._result.ct_log_discovered = getattr(_ct_outcome_prelude, "ct_results_raw", 0) or 0
            _ct_results_raw = getattr(_ct_outcome_prelude, "ct_results_raw", 0) or 0
            _ct_error = getattr(_ct_outcome_prelude, "error", None)
            _accepted = 0
            _attempted_lanes.append("CT")
            if _ct_results_raw > 0 and _accepted == 0:
                _terminal_lanes.append("CT")
            elif _ct_results_raw == 0 and _ct_error is None:
                _terminal_lanes.append("CT")
            self._result.ct_loss_stage = (
                _ct_telemetry_from_gather.get("loss_stage", "") if isinstance(_ct_telemetry_from_gather, dict) else ""
            )
            self._result.ct_bridge_invoked = (
                _ct_telemetry_from_gather.get("bridge_invoked", False)
                if isinstance(_ct_telemetry_from_gather, dict)
                else False
            )
            _prelude_keys = (
                _ct_telemetry_from_gather.get("prelude_keys", ()) if isinstance(_ct_telemetry_from_gather, dict) else ()
            )
            self._result.ct_raw_sample_keys = _prelude_keys
            self._result.ct_raw_sample_count = _ct_results_raw
            self._result.ct_raw_count = _ct_results_raw
            self._result.ct_candidates_built = (
                _ct_telemetry_from_gather.get("candidates_count", 0)
                if isinstance(_ct_telemetry_from_gather, dict)
                else 0
            )
            _rejections_prelude = getattr(_ct_outcome_prelude, "rejection_reasons", ()) or ()
            self._result.ct_bridge_rejections_count = len(_rejections_prelude)
            self._result.ct_bridge_rejection_reasons = tuple(
                (str(r) for r in (_rejections_prelude[:3] if _rejections_prelude else []))
            )
            self._result.ct_candidates_accumulated = 0
            self._result.ct_candidates_stored = 0
            self._result.ct_storage_rejected = 0
            if isinstance(_ct_telemetry_from_gather, dict):
                self._result.ct_candidate_count = _ct_telemetry_from_gather.get("ct_bridge_candidate_count", 0)
                self._result.ct_valid_domain_count = _ct_telemetry_from_gather.get("ct_bridge_valid_domain_count", 0)
                self._result.ct_bridge_build_success_count = _ct_telemetry_from_gather.get(
                    "ct_bridge_build_success_count", 0
                )
                self._result.ct_bridge_quality_rejected_count = _ct_telemetry_from_gather.get(
                    "ct_bridge_quality_rejected_count", 0
                )
            _ct_quarantine_count = (
                _ct_telemetry_from_gather.get("ct_quarantine_count", 0)
                if isinstance(_ct_telemetry_from_gather, dict)
                else 0
            )
            _ct_quarantine_entries = (
                _ct_telemetry_from_gather.get("ct_quarantine_entries", [])
                if isinstance(_ct_telemetry_from_gather, dict)
                else []
            )
            self._result.ct_quarantine_count = _ct_quarantine_count
            if _ct_quarantine_entries:
                _samples: list[str] = []
                for _entry in _ct_quarantine_entries[:10]:
                    try:
                        _samples.append(_msgspec_encode(_entry).decode())
                    except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        pass
                self._result.ct_quarantine_samples = tuple(_samples)
            for _entry in _ct_quarantine_entries:
                try:
                    self._nonfeed_ledger.add_ct_quarantine(
                        domain=_entry.get("raw_value", ""),
                        reject_reason=_entry.get("reject_reason", "unknown"),
                        source_url=_entry.get("source_url", ""),
                        query=_entry.get("normalized_query", ""),
                    )
                except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                    pass
        pass
        pass
        if _is_nonfeed_diagnostic and (not _nonfeed_prelude_done):
            _hardware_critical = _uma in ("critical", "emergency")
            _nonfeed_expected: list[str] = []
            _nonfeed_lanes_to_run: list[tuple[str, bool, float, int]] = []
            if self._acquisition_plan is not None:
                for _plan in getattr(self._acquisition_plan, "plans", []):
                    _lane_name = getattr(_plan.lane, "value", str(_plan.lane))
                    _enabled = getattr(_plan, "enabled", False)
                    _timeout = getattr(_plan, "timeout_s", 20.0)
                    _max_items = getattr(_plan, "max_items", 5)
                    if _lane_name in ("WAYBACK", "PASSIVE_DNS", "PIVOT_EXECUTOR", "DOH"):
                        if _enabled:
                            _nonfeed_lanes_to_run.append((_lane_name, True, _timeout, _max_items))
                        else:
                            _nonfeed_prelude_skipped[_lane_name] = "plan_disabled"
                        _nonfeed_expected.append(_lane_name)
            try:
                _effective_duration = getattr(self._config, "duration_s", 300.0)
                _windup_lead = self._config.effective_windup_lead_s
                _active_window = max(_effective_duration - _windup_lead, 30.0)
                _prelude_budget_s = min(_active_window * 0.3, 45.0)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                _prelude_budget_s = min(max(30.0, _time.monotonic() - _t0) * 0.4, 45.0)
            _nonfeed_prelude_expected = _nonfeed_expected
            _pivot_lanes: Any = None
            if self._pivot_planner is not None:
                try:
                    from hledac.universal.pipeline.pivot_lane_planner import plan_lanes_for_pivot_seeds
                    from hledac.universal.runtime.pivot_planner import (
                        generate_pivot_candidates_from_query as _gen_pivots,
                    )

                    _pivot_seeds = _gen_pivots(query)
                    if _pivot_seeds:
                        _seed_dicts = [
                            {"value": p.ioc_value, "seed_type": p.ioc_type}
                            for p in _pivot_seeds
                            if p.ioc_value and p.ioc_type
                        ]
                        if _seed_dicts:
                            _pivot_plan = plan_lanes_for_pivot_seeds(_seed_dicts)
                            _pivot_lanes = getattr(_pivot_plan, "items", None)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    _pivot_lanes = None
            try:
                import re as _re

                _speculative_domain_re = _re.compile(
                    "\\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?\\.)+(?:com|org|net|io|onion|xyz|app|dev|info|me|cc|biz|co|tv|ai|cy|su|ua|ro|hr|si|click|link|top|icu|buzz|stream|live|news|film|pub|club|social|blog|vip|pro|ru|cn|uk|de|fr|nl|br|in|au|ca|eu|org|net|edu|gov|mil|arpa|adsl|bbs)\\b",
                    _re.IGNORECASE,
                )
                _speculative_noise = frozenset(
                    {
                        "example",
                        "test",
                        "localhost",
                        "invalid",
                        "sample",
                        "foo",
                        "bar",
                        "baz",
                        "qux",
                        "demo",
                        "placeholder",
                        "domain",
                    }
                )
                _raw_domains = _speculative_domain_re.findall(query.lower())
                _cleaned_domains = [d for d in _raw_domains if d not in _speculative_noise and len(d) > 4]
                if _cleaned_domains:
                    self._result.pivot_seed_domains = tuple(_cleaned_domains[:10])
                    self._result.seed_context_available = True
                    self._result.seed_context_propagated = True
                    self._result.lanes_unlocked_by_seed_context = ["CT", "DOH", "WAYBACK", "PASSIVE_DNS"]
                    self._result.seed_context_skip_reason = ""
                    self._result.seed_context_source = "speculative_query_extract"
                    log.debug("[P0-1] Speculative domain extraction: seeds=%s", self._result.pivot_seed_domains)
            except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                pass
            if not _cleaned_domains:
                try:
                    from hledac.universal.runtime.nonfeed_candidate_ledger import generate_conceptual_domain_candidates

                    _mlx_candidates = await generate_conceptual_domain_candidates(query)
                    if _mlx_candidates:
                        _mlx_domains = [c.domain for c in _mlx_candidates[:10] if c.domain]
                        if _mlx_domains:
                            _cleaned_domains = _mlx_domains
                            log.debug("[P2-4] MLX conceptual domains fallback: seeds=%s", _cleaned_domains)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            if not _cleaned_domains and duckdb_store is not None:
                try:
                    if hasattr(duckdb_store, "_conn"):
                        _kw_clause = " OR ".join(
                            (
                                f"LOWER(query_text) LIKE '%{kw}%'"
                                for kw in [w for w in query.lower().split() if len(w) >= 4][:5]
                            )
                        )
                        if _kw_clause:
                            _hist_sql = f"\n                                SELECT DISTINCT value AS domain\n                                FROM canonical_findings,\n                                     LATERAL (SELECT json_each_text(payload_text::JSON) WHERE key = 'domain') AS j\n                                WHERE ({_kw_clause})\n                                  AND sprint_id != '{getattr(self, '_sprint_id', 'current')}'\n                                ORDER BY discovered_at DESC\n                                LIMIT 10\n                            "
                            try:
                                _hist_rows = await duckdb_store.async_execute_raw_sql(_hist_sql)
                                _historical_domains = [r[0] for r in _hist_rows if r[0]]
                                if _historical_domains:
                                    _cleaned_domains = _historical_domains
                                    log.debug("[P2-4] DuckDB historical seed fallback: seeds=%s", _cleaned_domains)
                            except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                                pass
                except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                    pass
            from hledac.universal.runtime.nonfeed_seed_runtime import (
                run_runtime_pivot_prelude as _run_runtime_pivot_prelude,
            )

            _pivot_profile_active = _is_nonfeed_diagnostic or _is_deep_osint_m1
            _speculative_had_seeds = (
                getattr(self._result, "seed_context_available", False)
                and getattr(self._result, "seed_context_source", "") == "speculative_query_extract"
            )
            if not _speculative_had_seeds:
                try:
                    _pivot_result = await _run_runtime_pivot_prelude(
                        query=query,
                        duckdb_store=duckdb_store,
                        nonfeed_diagnostic_active=_pivot_profile_active,
                        existing_findings=None,
                        acquisition_profile=self._config.acquisition_profile or "default",
                    )
                    self._result.pivot_seed_domains = _pivot_result.get("pivot_seed_domains", ())
                    self._result.pivot_seed_ips = _pivot_result.get("pivot_seed_ips", ())
                    self._result.pivot_seed_urls = _pivot_result.get("pivot_seed_urls", ())
                    self._result.pivot_seed_hashes = _pivot_result.get("pivot_seed_hashes", ())
                    self._result.pivot_seed_cves = _pivot_result.get("pivot_seed_cves", ())
                    self._result.seed_context_available = _pivot_result.get("seed_context_available", False)
                    self._result.seed_context_propagated = _pivot_result.get("seed_context_propagated", False)
                    self._result.lanes_unlocked_by_seed_context = _pivot_result.get(
                        "lanes_unlocked_by_seed_context", []
                    )
                    self._result.seed_context_skip_reason = _pivot_result.get("seed_context_skip_reason", "")
                    self._result.seed_context_source = _pivot_result.get("seed_context_source", "")
                except Exception:  # noqa: BLE001 — best-effort; lock acquisition failure; non-critical
                    pass
            else:
                log.debug("[P0-1] Skipping run_runtime_pivot_prelude: speculative extraction found seeds")
            if not _speculative_had_seeds:
                self._result.seed_quality_checked = _pivot_result.get("seed_quality_checked", False)
                self._result.seed_quality_keep_count = _pivot_result.get("seed_quality_keep_count", 0)
                self._result.seed_quality_drop_count = _pivot_result.get("seed_quality_drop_count", 0)
                self._result.seed_quality_drop_reasons = _pivot_result.get("seed_quality_drop_reasons", {})
                self._result.seed_quality_kept_sample = _pivot_result.get("seed_quality_kept_sample", [])
                self._result.seed_quality_dropped_sample = _pivot_result.get("seed_quality_dropped_sample", [])
                self._result.seed_quality_bypass_reason = _pivot_result.get("seed_quality_bypass_reason", "")
            if not self._result.seed_context_available:
                import re as _re

                _is_domain_query = bool(
                    _re.search("\\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?\\.)+[a-zA-Z]{2,}\\b", query)
                )
                if _is_domain_query and (not self._result.seed_context_available):
                    from hledac.universal.runtime.nonfeed_seed_runtime import (
                        _should_allow_low_quality_seed_for_profile,
                        classify_seed_quality,
                    )

                    _domain = query.strip().lower()
                    _is_low_quality = not _should_allow_low_quality_seed_for_profile()
                    if _is_low_quality:
                        from hledac.universal.runtime.nonfeed_seed_extractor import NonfeedSeed

                        _fake_seed = NonfeedSeed(
                            value=_domain, kind="domain", source="query", confidence=1.0, reason="domain_query_fallback"
                        )
                        _quality = classify_seed_quality(_fake_seed, query=query, context="")
                        if _quality.decision == "drop":
                            log.debug(
                                "[F241B] domain fallback blocked by quality gate: domain=%s reason=%s",
                                _domain,
                                _quality.reason,
                            )
                            self._result.seed_quality_drop_count = (
                                getattr(self._result, "seed_quality_drop_count", 0) + 1
                            )
                            _existing_drop_reasons = getattr(self._result, "seed_quality_drop_reasons", {})
                            _reason_key = _quality.reason or "domain_fallback_dropped"
                            _existing_drop_reasons[_reason_key] = _existing_drop_reasons.get(_reason_key, 0) + 1
                            self._result.seed_quality_drop_reasons = _existing_drop_reasons
                            self._result.seed_quality_bypass_reason = ""
                        else:
                            self._result.pivot_seed_domains = (_domain,)
                            self._result.seed_context_available = True
                            self._result.seed_context_propagated = True
                            self._result.lanes_unlocked_by_seed_context = ["CT", "DOH", "WAYBACK", "PASSIVE_DNS"]
                            self._result.seed_context_skip_reason = ""
                            self._result.seed_context_source = "domain_query_fallback"
                            self._result.seed_quality_keep_count = (
                                getattr(self._result, "seed_quality_keep_count", 0) + 1
                            )
                            self._result.seed_quality_checked = True
                    else:
                        self._result.pivot_seed_domains = (_domain,)
                        self._result.seed_context_available = True
                        self._result.seed_context_propagated = True
                        self._result.lanes_unlocked_by_seed_context = ["CT", "DOH", "WAYBACK", "PASSIVE_DNS"]
                        self._result.seed_context_skip_reason = ""
                        self._result.seed_context_source = "domain_query_fallback"
                        self._result.seed_quality_bypass_reason = "diagnostic_profile"
                elif not _is_domain_query and (not self._result.seed_context_available):
                    try:
                        import re as _re

                        _noise = {
                            "the",
                            "a",
                            "an",
                            "of",
                            "for",
                            "in",
                            "on",
                            "at",
                            "to",
                            "and",
                            "or",
                            "is",
                            "are",
                            "was",
                            "were",
                            "be",
                            "been",
                            "being",
                            "have",
                            "has",
                            "had",
                            "do",
                            "does",
                            "did",
                            "will",
                            "would",
                            "could",
                            "should",
                            "may",
                            "might",
                            "must",
                            "can",
                            "with",
                            "by",
                            "from",
                            "that",
                            "this",
                        }
                        _tokens = _re.findall("\\b[a-zA-Z]{3,}\\b", query.lower())
                        _significant = [t for t in _tokens if t not in _noise]
                        if _significant:
                            _pseudo_seeds = tuple(_significant[:5])
                            self._result.pivot_seed_domains = _pseudo_seeds
                            self._result.seed_context_available = True
                            self._result.seed_context_propagated = True
                            self._result.lanes_unlocked_by_seed_context = ["PUBLIC", "CT", "DOH"]
                            self._result.seed_context_skip_reason = ""
                            self._result.seed_context_source = "query_term_fallback"
                    except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                        pass
            _planner_seed_iocs: dict = getattr(self, "_planner_seed_iocs", {}) or {}
            if _planner_seed_iocs and self._result.planner_action_skip_reason in ("", "no_iocs_extracted", None):
                _pa_doms: tuple = _planner_seed_iocs.get("domains", ())
                _pa_ips: tuple = _planner_seed_iocs.get("ips", ())
                _pa_urls: tuple = _planner_seed_iocs.get("urls", ())
                if _pa_doms or _pa_ips or _pa_urls:
                    _existing_doms = getattr(self._result, "pivot_seed_domains", ()) or ()
                    _existing_ips = getattr(self._result, "pivot_seed_ips", ()) or ()
                    _existing_urls = getattr(self._result, "pivot_seed_urls", ()) or ()
                    _combined_doms = tuple(_existing_doms) + _pa_doms
                    _combined_ips = tuple(_existing_ips) + _pa_ips
                    _combined_urls = tuple(_existing_urls) + _pa_urls
                    self._result.pivot_seed_domains = _combined_doms[:10]
                    self._result.pivot_seed_ips = _combined_ips[:10]
                    self._result.pivot_seed_urls = _combined_urls[:10]
                    self._result.seed_context_available = True
                    self._result.seed_context_propagated = True
                    log.debug(
                        "[F237B] planner_action seeds merged: domains=%s, ips=%s, urls=%s", _pa_doms, _pa_ips, _pa_urls
                    )
            self._result.nonfeed_profile_expected_lanes = (
                getattr(self._result, "nonfeed_profile_expected_lanes", ()) or ()
            )
            _unlocked: list[str] = getattr(self._result, "lanes_unlocked_by_seed_context", []) or []
            _planner_lanes_result: list[str] = getattr(self._result, "planner_action_lanes_requested", []) or []
            for _pl in _planner_lanes_result:
                if _pl not in _unlocked:
                    _unlocked.append(_pl)
            if _unlocked and self._result.seed_context_available:
                _existing: set[str] = {lane for lane, *_ in _nonfeed_lanes_to_run}
                _expected: set[str] = set(self._result.nonfeed_profile_expected_lanes)
                _seed_lane_defaults: dict[str, tuple[float, int]] = {
                    "DOH": (12.0, 8),
                    "CT": (18.0, 10),
                    "WAYBACK": (18.0, 8),
                    "PASSIVE_DNS": (12.0, 8),
                }
                for _lane in ("CT", "DOH", "WAYBACK", "PASSIVE_DNS"):
                    if _lane not in _unlocked:
                        continue
                    if _lane in _existing:
                        continue
                    if _lane not in _expected:
                        continue
                    _timeout_s, _max_items = _seed_lane_defaults.get(_lane, (20.0, 5))
                    if _hardware_critical and _lane in ("DOH", "WAYBACK"):
                        _timeout_s = min(_timeout_s, 10.0)
                        _max_items = min(_max_items, 3)
                    if _is_deep_osint_m1 and _uma in ("warn", "critical", "emergency"):
                        _nonfeed_prelude_skipped[_lane] = "deep_osint_m1_memory_throttled"
                        continue
                    _nonfeed_lanes_to_run.append((_lane, True, _timeout_s, _max_items))
                    _nonfeed_expected.append(_lane)
            _query_domain_candidates: list[str] = []
            if query and isinstance(query, str) and query.strip():
                _candidates = extract_domain_candidates_from_text(
                    query, source_url=None, source_family="query", min_confidence=0.3
                )
                _query_domain_candidates = [c.domain for c in _candidates if c.confidence >= 0.3]
            if not _query_domain_candidates and query and isinstance(query, str):
                try:
                    from hledac.universal.runtime.nonfeed_candidate_ledger import generate_conceptual_domain_candidates

                    _mlx_candidates = await generate_conceptual_domain_candidates(query)
                    _query_domain_candidates = [c.domain for c in _mlx_candidates if c.domain]
                except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                    pass
            if not _query_domain_candidates and query and isinstance(query, str):
                from hledac.universal.runtime.nonfeed_seed_runtime import _decompose_query_keywords_to_seeds

                _rule_seeds: list[str] = _decompose_query_keywords_to_seeds(query)
                if _rule_seeds:
                    _query_domain_candidates = _rule_seeds
            if not _query_domain_candidates and query and isinstance(query, str):
                try:
                    from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
                    from hledac.universal.tools.url_dedup import extract_domain as extract_domain_from_url

                    _serp_result = await async_search_public_web(query, max_results=10, timeout_s=15.0)
                    if _serp_result and _serp_result.hits:
                        _serp_domains: list[str] = []
                        for _hit in _serp_result.hits[:10]:
                            if getattr(_hit, "url", None):
                                _d = extract_domain_from_url(_hit.url)
                                if _d:
                                    _serp_domains.append(_d)
                        if _serp_domains:
                            _query_domain_candidates = _serp_domains
                except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                    pass
            _synthetic_domains: list[str] = []
            _seed_ctx: NonfeedSeedContext | None = None
            _pivot_has_seeds = (
                self._result.pivot_seed_domains or self._result.pivot_seed_ips or self._result.pivot_seed_urls
            )
            _next_seeds_has_iocs = (
                self._result.next_seeds_ioc_domains
                or self._result.next_seeds_ioc_ips
                or self._result.next_seeds_ioc_urls
                or self._result.next_seeds_ioc_hashes
            )
            if _pivot_has_seeds or _next_seeds_has_iocs:
                _duckpgq_seeds: tuple[dict, ...] = ()
                try:
                    _csm = get_cross_sprint_memory()
                    if _csm.is_available():
                        _all_seed_values = list(
                            (self._result.pivot_seed_domains or ())
                            + (self._result.pivot_seed_ips or ())
                            + (self._result.next_seeds_ioc_domains or ())
                            + (self._result.next_seeds_ioc_ips or ())
                        )
                        if _all_seed_values:
                            _duckpgq_results = _csm.get_related_entities_batch(_all_seed_values)
                            _flat: list[dict] = []
                            for _entities in _duckpgq_results.values():
                                _flat.extend(_entities)
                            _duckpgq_seeds = tuple(_flat[:20])
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
                _seed_ctx = NonfeedSeedContext(
                    domains=tuple(
                        (_synthetic_domains or ())
                        + tuple(_query_domain_candidates or [])
                        + (self._result.pivot_seed_domains or ())
                        + (self._result.next_seeds_ioc_domains or ())
                    ),
                    ips=tuple((self._result.pivot_seed_ips or ()) + (self._result.next_seeds_ioc_ips or ())),
                    urls=tuple((self._result.pivot_seed_urls or ()) + (self._result.next_seeds_ioc_urls or ())),
                    hashes=tuple((self._result.pivot_seed_hashes or ()) + (self._result.next_seeds_ioc_hashes or ())),
                    cves=tuple((self._result.pivot_seed_cves or ()) + (self._result.next_seeds_ioc_cves or ())),
                    duckpgq_entities=_duckpgq_seeds,
                )
            await self._run_nonfeed_prelude_gather(
                query=query,
                duckdb_store=duckdb_store,
                ct_log_client=ct_log_client,
                nonfeed_lanes_to_run=_nonfeed_lanes_to_run,
                hardware_critical=_hardware_critical,
                t0=_t0,
                time_module=_time,
                prelude_budget_s=_prelude_budget_s,
                nonfeed_prelude_attempted=_nonfeed_prelude_attempted,
                nonfeed_prelude_terminal=_nonfeed_prelude_terminal,
                nonfeed_prelude_skipped=_nonfeed_prelude_skipped,
                nonfeed_prelude_accepted=_nonfeed_prelude_accepted,
                nonfeed_prelude_errors=_nonfeed_prelude_errors,
                pivot_lanes=_pivot_lanes,
                seed_context=_seed_ctx,
            )
            _nonfeed_prelude_done = True
            if _has_domain and (not self._result.seed_context_available) and _is_nonfeed_diagnostic:
                log.critical(
                    "[F300S-P1] domain_detected=True but seed_context_available=False (acquisition_profile=%s, query=%r) -- skipping windup, entering early-exit",
                    self._config.acquisition_profile or "default",
                    query[:80],
                )
                self._result.acquisition_prelude_ran = True
                self._result.acquisition_prelude_reason = "domain_detected_no_seeds_early_abort"
                self._result.acquisition_prelude_duration_s = _time.monotonic() - _t0
                self._runner.abort("domain_detected_no_seeds")
                return
        if _prelude_outcomes:
            self._lane_outcomes = tuple(_prelude_outcomes)
            self._result.acquisition_lane_outcomes = tuple(_prelude_outcomes)
            self._accumulate_lane_findings(tuple(_prelude_outcomes), query)
        self._result.acquisition_prelude_ran = True
        self._result.acquisition_prelude_terminal_lanes = tuple(_terminal_lanes)
        self._result.acquisition_prelude_skipped_lanes = dict(_skipped)
        self._result.acquisition_prelude_errors = dict(_errors)
        self._result.acquisition_prelude_duration_s = _time.monotonic() - _t0
        _missing = [r for r in _required if r not in _terminal_lanes]
        self._result.acquisition_prelude_missing_lanes = tuple(_missing)
        if "CT" in _missing:
            self._result.ct_prelude_missing_but_final_attempted = getattr(self._result, "ct_scheduled", False)
        if _is_nonfeed_diagnostic:
            self._result.nonfeed_prelude_enabled = True
            self._result.nonfeed_prelude_expected_lanes = tuple(_nonfeed_prelude_expected)
            self._result.nonfeed_prelude_attempted_lanes = tuple(_nonfeed_prelude_attempted)
            self._result.nonfeed_prelude_terminal_lanes = tuple(_nonfeed_prelude_terminal)
            self._result.nonfeed_prelude_missing_lanes = tuple(
                (
                    k
                    for k in _nonfeed_prelude_expected
                    if k not in _nonfeed_prelude_terminal and k not in _nonfeed_prelude_skipped
                )
            )
            self._result.nonfeed_prelude_accepted_by_lane = dict(_nonfeed_prelude_accepted)
            self._result.nonfeed_prelude_error_by_lane = dict(_nonfeed_prelude_errors)
            self._result.nonfeed_prelude_duration_s = self._result.acquisition_prelude_duration_s
            self._result.nonfeed_prelude_feed_blocked_until_complete = True
        if query and isinstance(query, str) and query.strip():
            try:
                from hledac.universal.runtime.nonfeed_candidate_ledger import extract_domain_candidates_from_text

                _h3_prewarm_candidates = extract_domain_candidates_from_text(
                    query, source_url=None, source_family="query", min_confidence=0.3
                )
                _h3_prewarm_domains = [c.domain for c in _h3_prewarm_candidates if c.confidence >= 0.3][:5]
                for _domain in _h3_prewarm_domains:
                    try:
                        probe_altsvc_speculative(f"https://{_domain}")
                    except Exception:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
                        pass
            except Exception:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
                pass
        log.debug(
            "[F209A] Acquisition prelude done: required=%s, terminal=%s, missing=%s, skipped=%s, errors=%s, dur=%.2fs",
            _required,
            _terminal_lanes,
            _missing,
            _skipped,
            _errors,
            self._result.acquisition_prelude_duration_s,
        )

    async def _run_nonfeed_prelude_gather(
        self,
        query: str,
        duckdb_store: Any,
        ct_log_client: Any,
        nonfeed_lanes_to_run: list[tuple[str, bool, float, int]],
        hardware_critical: bool,
        t0: float,
        time_module: Any,
        prelude_budget_s: float,
        nonfeed_prelude_attempted: list[str],
        nonfeed_prelude_terminal: list[str],
        nonfeed_prelude_skipped: dict[str, str],
        nonfeed_prelude_accepted: dict[str, int],
        nonfeed_prelude_errors: dict[str, str],
        pivot_lanes: Sequence[Any] | None = None,
        seed_context: NonfeedSeedContext | None = None,
    ) -> None:
        """

        Sprint F233D / F228A: Run nonfeed prelude lanes concurrently (Semaphore(3) for M1 8GB).



        F220J: pivot_lanes provides pre-computed LanePlanItem tuples from

        plan_lanes_for_pivot_seeds(). If None, pivots are generated inline.



        F228A: seed_context enables domain/IP shaping for text queries via build_lane_query.

        DOH lane uses pivot domain seed instead of raw query when available.

        """
        _sem = asyncio.Semaphore(3)

        async def _run_lane(_lane_name: str, _lane_timeout: float, _lane_max_items: int) -> tuple[str, int]:
            async with _sem:
                if time_module.monotonic() - t0 >= prelude_budget_s:
                    nonfeed_prelude_skipped[_lane_name] = "prelude_budget_exceeded"
                    return (_lane_name, 0)
                try:
                    async with asyncio.timeout(min(_lane_timeout, 20.0)):
                        if _lane_name == "WAYBACK":
                            return await self._run_wayback_prelude_lane(
                                query,
                                duckdb_store,
                                time_module,
                                nonfeed_prelude_attempted,
                                nonfeed_prelude_terminal,
                                nonfeed_prelude_accepted,
                                nonfeed_prelude_skipped,
                                seed_context=seed_context,
                            )
                        elif _lane_name == "PASSIVE_DNS":
                            return await self._run_pdns_prelude_lane(
                                query,
                                duckdb_store,
                                time_module,
                                nonfeed_prelude_attempted,
                                nonfeed_prelude_terminal,
                                nonfeed_prelude_accepted,
                                seed_context=seed_context,
                            )
                        elif _lane_name == "DOH":
                            return await self._run_doh_prelude_lane(
                                query,
                                duckdb_store,
                                time_module,
                                nonfeed_prelude_attempted,
                                nonfeed_prelude_terminal,
                                nonfeed_prelude_accepted,
                                pivot_doh_items=pivot_lanes,
                                seed_context=seed_context,
                            )
                        elif _lane_name == "CT":
                            from hledac.universal.runtime.acquisition_strategy import AcquisitionLane, build_lane_query

                            _ct_shaped = build_lane_query(query, AcquisitionLane.CT, seed_context=seed_context)
                            if isinstance(_ct_shaped, dict) or not _ct_shaped:
                                nonfeed_prelude_skipped["CT"] = "empty_ct_query"
                                return ("CT", 0)
                            _ct_adapter = _get_ct_adapter()
                            try:
                                async with asyncio.timeout(min(_lane_timeout, 20.0)):
                                    _ct_result, _ct_outcome = await _ct_adapter(
                                        query=_ct_shaped,
                                        max_results=_lane_max_items,
                                        timeout_s=min(_lane_timeout, 15.0),
                                    )
                                nonfeed_prelude_attempted.append("CT")
                                nonfeed_prelude_terminal.append("CT")
                                _accepted_ct = getattr(_ct_outcome, "accepted_count", 0) or 0
                                nonfeed_prelude_accepted["CT"] = _accepted_ct
                                return ("CT", _accepted_ct)
                            except TimeoutError:
                                nonfeed_prelude_errors["CT"] = "prelude_timeout"
                                nonfeed_prelude_skipped["CT"] = "prelude_timeout"
                                return ("CT", 0)
                            except Exception as _ct_exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                nonfeed_prelude_errors["CT"] = f"{type(_ct_exc).__name__}:{_ct_exc}"
                                nonfeed_prelude_skipped["CT"] = f"prelude_error:{type(_ct_exc).__name__}"
                                return ("CT", 0)
                        elif _lane_name == "PIVOT_EXECUTOR":
                            from hledac.universal.runtime.pivot_planner import generate_pivot_candidates_from_query

                            _gs = self._get_pivot_graph_stats_for_planning()
                            _pivots = generate_pivot_candidates_from_query(query, graph_stats=_gs)
                            if _pivots:
                                nonfeed_prelude_attempted.append("PIVOT_EXECUTOR")
                                nonfeed_prelude_terminal.append("PIVOT_EXECUTOR")
                                return ("PIVOT_EXECUTOR", 0)
                            else:
                                nonfeed_prelude_skipped["PIVOT_EXECUTOR"] = "no_candidates"
                                return ("PIVOT_EXECUTOR", 0)
                        else:
                            return (_lane_name, 0)
                except TimeoutError:
                    nonfeed_prelude_errors[_lane_name] = "prelude_timeout"
                    nonfeed_prelude_skipped[_lane_name] = "prelude_timeout"
                    return (_lane_name, 0)
                except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    nonfeed_prelude_errors[_lane_name] = f"{type(_exc).__name__}:{_exc}"
                    nonfeed_prelude_skipped[_lane_name] = f"prelude_error:{type(_exc).__name__}"
                    return (_lane_name, 0)

        _tasks = [
            _run_lane(_name, _timeout, _max_items) for _name, _enabled, _timeout, _max_items in nonfeed_lanes_to_run
        ]
        if _tasks:
            _lane_results = await safe_gather_ok(*_tasks, label="sprint_scheduler:12263")
            for _result in _lane_results:
                if isinstance(_result, BaseException):
                    if isinstance(_result, asyncio.CancelledError):
                        raise _result
                    log.warning("nonfeed prelude gather exception: %s", _result)
                elif isinstance(_result, tuple):
                    _ln, _la = _result
                    nonfeed_prelude_accepted[_ln] = _la

    async def _run_public_prelude_lane(self, query: str) -> dict:
        """Run PUBLIC prelude lane. Returns result dict, never raises."""
        import asyncio

        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
        from hledac.universal.runtime.acquisition_strategy import AcquisitionLane, build_lane_query

        try:
            _shaped = build_lane_query(query, AcquisitionLane.PUBLIC)
            if isinstance(_shaped, dict) or not _shaped:
                return {
                    "lane": "PUBLIC",
                    "attempted": False,
                    "skipped": True,
                    "skip_reason": "empty_public_query",
                    "raw_count": 0,
                    "built_count": 0,
                    "accepted_count": 0,
                    "error": None,
                    "timeout": False,
                    "duration_s": None,
                }
            async with asyncio.timeout(10.0):
                _pipeline_result = await async_run_live_public_pipeline(
                    query=_shaped,
                    store=None,
                    max_results=3,
                    fetch_timeout_s=10.0,
                    fetch_concurrency=2,
                    hermes_engine=None,
                    memory_manager=None,
                    enqueue_hypothesis_pivot=None,
                )
            return {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": False,
                "skip_reason": None,
                "raw_count": getattr(_pipeline_result, "discovered", 0) or 0,
                "built_count": getattr(_pipeline_result, "fetched", 0) or 0,
                "accepted_count": getattr(_pipeline_result, "accepted_findings", 0) or 0,
                "error": getattr(_pipeline_result, "error", None),
                "timeout": getattr(_pipeline_result, "timed_out", False),
                "duration_s": getattr(_pipeline_result, "elapsed_s", None),
            }
        except TimeoutError:
            return {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": False,
                "timeout": True,
                "error": None,
                "duration_s": 10.0,
            }
        except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": False,
                "error": f"{type(exc).__name__}:{exc}",
                "timeout": False,
                "duration_s": None,
            }

    async def _run_ct_prelude_lane(self, query: str, seed_ctx: Any) -> tuple:
        """Run CT prelude lane. Returns (AcquisitionLaneOutcome, ct_result, ct_telemetry)."""
        import asyncio
        import time as _time

        from hledac.universal.runtime.acquisition_strategy import (
            AcquisitionLane,
            AcquisitionLaneOutcome,
            build_lane_query,
        )
        from hledac.universal.runtime.source_finding_bridge import ct_results_to_findings

        _ct_outcome_prelude: Any = None
        _ct_result: Any = None
        _candidates_prelude: tuple = ()
        _rejections_prelude: tuple = ()
        _ct_telemetry_prelude: Any = None
        try:
            _shaped = build_lane_query(query, AcquisitionLane.CT, seed_context=seed_ctx)
            if isinstance(_shaped, dict) or not _shaped:
                return (None, None, None)
            _ct_call = _get_ct_adapter()
            async with asyncio.timeout(15.0):
                _ct_result, _ct_outcome_obj = await _ct_call(query=_shaped, max_results=5, timeout_s=15.0)
            _ct_results_raw = getattr(_ct_outcome_obj, "raw_count", 0) or 0
            _ct_error = getattr(_ct_outcome_obj, "error", None)
            _candidates_prelude, _rejections_prelude, _ct_telemetry_prelude = ct_results_to_findings(
                _ct_result, _ct_outcome_obj, query, sprint_id=f"prelude-{int(_time.time())}"
            )
            _accepted = 0
            _ct_outcome_prelude = AcquisitionLaneOutcome(
                lane=AcquisitionLane.CT,
                enabled=True,
                attempted=True,
                accepted_findings=_accepted,
                produced_items=_ct_results_raw,
                duration_s=0.0,
                source_family="ct",
                ct_query=str(_shaped),
                ct_results_raw=_ct_results_raw,
                error=_ct_error,
                candidate_findings=tuple(_candidates_prelude),
                rejection_reasons=tuple(_rejections_prelude),
                rejected_count=len(_rejections_prelude),
                sample_rejections=tuple(_rejections_prelude[:3]),
            )
        except TimeoutError:
            _ct_outcome_prelude = AcquisitionLaneOutcome(
                lane=AcquisitionLane.CT,
                enabled=True,
                attempted=True,
                timeout=True,
                duration_s=15.0,
                error="prelude_timeout",
                source_family="ct",
                ct_query=str(_shaped) if "_shaped" in dir() else "",
                ct_results_raw=0,
            )
        except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            _ct_outcome_prelude = None
        _prelude_candidates = len(_candidates_prelude) if _candidates_prelude else 0
        if _ct_results_raw == 0:
            _loss_stage = "NO_RAW"
            _bridge_invoked = False
        elif _prelude_candidates == 0 and _ct_results_raw > 0 and (REJECTION_UNSUPPORTED_SHAPE in _rejections_prelude):
            _loss_stage = "UNSUPPORTED_RAW_SHAPE"
            _bridge_invoked = True
        elif _prelude_candidates == 0 and _ct_results_raw > 0:
            _loss_stage = "ALL_REJECTED_BY_BRIDGE"
            _bridge_invoked = True
        else:
            _loss_stage = "CANDIDATES_BUILT_NOT_ACCUMULATED"
            _bridge_invoked = True
        _prelude_keys: tuple = ()
        if _ct_results_raw > 0 and hasattr(_ct_result, "hits") and _ct_result.hits:
            _prelude_keys = tuple(
                sorted(
                    {
                        k
                        for hit in list(_ct_result.hits)[:3]
                        for k in (getattr(hit, "url", None), getattr(hit, "ct_name_value", None))
                        if k
                    }
                )
            )
        _ct_quarantine_count = 0
        _ct_quarantine_entries: list = []
        if isinstance(_ct_telemetry_prelude, dict):
            _ct_quarantine_count = _ct_telemetry_prelude.get("ct_quarantine_count", 0)
            _ct_quarantine_entries = _ct_telemetry_prelude.get("ct_quarantine_entries", [])
        _telemetry = {
            "loss_stage": _loss_stage,
            "bridge_invoked": _bridge_invoked,
            "prelude_keys": _prelude_keys,
            "candidates_count": _prelude_candidates,
            "ct_quarantine_count": _ct_quarantine_count,
            "ct_quarantine_entries": _ct_quarantine_entries,
        }
        if isinstance(_ct_telemetry_prelude, dict):
            _telemetry.update(
                {
                    "ct_bridge_candidate_count": _ct_telemetry_prelude.get("ct_bridge_candidate_count", 0),
                    "ct_bridge_valid_domain_count": _ct_telemetry_prelude.get("ct_bridge_valid_domain_count", 0),
                    "ct_bridge_build_success_count": _ct_telemetry_prelude.get("ct_bridge_build_success_count", 0),
                    "ct_bridge_quality_rejected_count": _ct_telemetry_prelude.get(
                        "ct_bridge_quality_rejected_count", 0
                    ),
                }
            )
        return (_ct_outcome_prelude, _ct_result, _telemetry)

    async def _run_wayback_prelude_lane(
        self,
        query: str,
        duckdb_store: Any,
        time_module: Any,
        nonfeed_prelude_attempted: list[str],
        nonfeed_prelude_terminal: list[str],
        nonfeed_prelude_accepted: dict[str, int],
        nonfeed_prelude_skipped: dict[str, str],
        seed_context: NonfeedSeedContext | None = None,
    ) -> tuple[str, int]:
        """Sprint F233D / F228A: WAYBACK prelude lane -- archive replay for pivot discovery.



        F228A: seed_context enables domain/IP shaping for text queries.

        """
        from hledac.universal.intelligence.wayback_diff_miner import WaybackDiffMiner
        from hledac.universal.runtime.acquisition_strategy import AcquisitionLane
        from hledac.universal.runtime.source_finding_bridge import wayback_results_to_findings

        _wb_query = build_lane_query(query, AcquisitionLane.WAYBACK, seed_context=seed_context)
        if _wb_query and (not isinstance(_wb_query, dict)):
            _wb_miner = WaybackDiffMiner()
            try:
                _wb_result = await _wb_miner.mine([str(_wb_query)])
            finally:
                await _wb_miner.close()
            _wb_cands, _wb_rejs, _wb_tel = wayback_results_to_findings(
                _wb_result, query, sprint_id=f"prelude-wb-{int(time_module.time())}"
            )
            if _wb_tel:
                self._result.wayback_advisory_clues_count += _wb_tel.get("wayback_changed_count", 0)
            nonfeed_prelude_attempted.append("WAYBACK")
            nonfeed_prelude_terminal.append("WAYBACK")
            _wb_acc = 0
            if _wb_cands and duckdb_store and hasattr(duckdb_store, "async_ingest_findings_batch"):
                _ing = None
                try:
                    if not self._enqueue_duckdb_write(duckdb_store, list(_wb_cands), self.sprint_id or ""):
                        _t = safe_create_task(
                            self._gate_then_ingest_and_accumulate(
                                duckdb_store, list(_wb_cands), sprint_id=self.sprint_id or ""
                            )
                        )
                        self._bg_tasks.add(_t)
                        _t.add_done_callback(self._bg_tasks.discard)
                    _wb_acc = sum((1 for r in _ing if isinstance(r, dict) and r.get("accepted"))) if _ing else 0
                except Exception as _exc:  # noqa: BLE001 — best-effort; callback handler; non-critical
                    log.warning(
                        "sprint %s: wayback ledger write failed -- %s: %s",
                        getattr(self._result, "sprint_id", "?"),
                        type(_exc).__name__,
                        _exc,
                    )
                    _wb_acc = 0
            nonfeed_prelude_accepted["WAYBACK"] = _wb_acc
            return ("WAYBACK", _wb_acc)
        nonfeed_prelude_skipped["WAYBACK"] = "empty_shaped_query" if not _wb_query else "lane_disabled"
        return ("WAYBACK", 0)

    async def _run_pdns_prelude_lane(
        self,
        query: str,
        duckdb_store: Any,
        time_module: Any,
        nonfeed_prelude_attempted: list[str],
        nonfeed_prelude_terminal: list[str],
        nonfeed_prelude_accepted: dict[str, int],
        seed_context: NonfeedSeedContext | None = None,
    ) -> tuple[str, int]:
        """Sprint F233D / F228A: PASSIVE_DNS prelude lane -- passive DNS recon.



        F228A: seed_context enables domain/IP shaping for text queries.

        """
        from hledac.universal.runtime.acquisition_strategy import AcquisitionLane
        from hledac.universal.runtime.source_finding_bridge import passive_dns_results_to_findings
        from hledac.universal.security.passive_dns import call_lookup_passive_dns

        _pdns_query = build_lane_query(query, AcquisitionLane.PASSIVE_DNS, seed_context=seed_context)
        if _pdns_query and (not isinstance(_pdns_query, dict)):
            _pdns_ips, _pdns_outcome = await call_lookup_passive_dns(str(_pdns_query))
            _pdns_cands, _pdns_rejs, _pdns_tel = passive_dns_results_to_findings(
                _pdns_ips, _pdns_outcome, query, sprint_id=f"prelude-pdns-{int(time_module.time())}"
            )
            if _pdns_tel:
                self._result.passive_dns_advisory_clues_count += _pdns_tel.get("pdns_public_accepted", 0)
            nonfeed_prelude_attempted.append("PASSIVE_DNS")
            nonfeed_prelude_terminal.append("PASSIVE_DNS")
            _pdns_acc = 0
            if _pdns_cands and duckdb_store and hasattr(duckdb_store, "async_ingest_findings_batch"):
                try:
                    if not self._enqueue_duckdb_write(duckdb_store, list(_pdns_cands), self.sprint_id or ""):
                        _t = safe_create_task(
                            self._gate_then_ingest_and_accumulate(
                                duckdb_store, list(_pdns_cands), sprint_id=self.sprint_id or ""
                            )
                        )
                        self._bg_tasks.add(_t)
                        _t.add_done_callback(self._bg_tasks.discard)
                except Exception:  # noqa: BLE001 — best-effort; callback handler; non-critical
                    pass
            nonfeed_prelude_accepted["PASSIVE_DNS"] = _pdns_acc
            return ("PASSIVE_DNS", _pdns_acc)
        nonfeed_prelude_skipped["PASSIVE_DNS"] = "empty_shaped_query" if not _pdns_query else "lane_disabled"
        return ("PASSIVE_DNS", 0)

    async def _run_doh_prelude_lane(
        self,
        query: str,
        duckdb_store: Any,
        time_module: Any,
        nonfeed_prelude_attempted: list[str],
        nonfeed_prelude_terminal: list[str],
        nonfeed_prelude_accepted: dict[str, int],
        pivot_doh_items: Sequence[Any] | None = None,
        seed_context: NonfeedSeedContext | None = None,
    ) -> tuple[str, int]:
        """Sprint F234A / F214 / F228A: DOH prelude lane -- DNS-over-HTTPS passive DNS recon.



        F220J: If pivot_doh_items contains a domain DOH item, use its seed_value

        directly instead of parsing the raw query. This ensures DOH prelude uses

        the domain extracted by the pivot planner, not a domain extracted from

        raw query text (which may be absent or wrong).



        F228A: seed_context enables domain/IP shaping for text queries when

        pivot_doh_items does not provide an explicit domain seed.



        Args:

            pivot_doh_items: Sequence of LanePlanItem with lane="DOH" from pivot plan.

                Each has seed_value, seed_type, priority, reason fields.

            seed_context: Optional NonfeedSeedContext from pivot/DuckDB extraction.

        """
        from hledac.universal.runtime.acquisition_strategy import AcquisitionLane, is_lane_enabled
        from hledac.universal.runtime.source_finding_bridge import doh_results_to_findings

        self._result.doh_planned = is_lane_enabled(self._acquisition_plan, AcquisitionLane.DOH)
        self._result.doh_scheduled = True
        _doh_domain: str | None = None
        if pivot_doh_items:
            for _item in pivot_doh_items:
                _lane = getattr(_item, "lane", None) or ""
                _seed_type = getattr(_item, "seed_type", None) or ""
                _seed_val = getattr(_item, "seed_value", None) or ""
                if _lane == "DOH" and _seed_type == "domain" and _seed_val:
                    _doh_domain = _seed_val
                    break
        if _doh_domain:
            _doh_query: Any = _doh_domain
            self._result.doh_seed_source = "pivot_plan"
        else:
            _doh_query = build_lane_query(query, AcquisitionLane.DOH, seed_context=seed_context)
            self._result.doh_seed_source = "seed_context" if seed_context and seed_context.domains else "raw_query"
            if _doh_query is None or (isinstance(_doh_query, dict) and _doh_query.get("_disabled")):
                self._result.doh_terminal_stage = "no_candidates"
                self._result.doh_seed_source = "no_domain_seed"
                nonfeed_prelude_accepted["DOH"] = 0
                return ("DOH", 0)
        if self._doh_adapter is None:
            try:
                from hledac.universal.intelligence.doh_lane import DOHAdapter

                self._doh_adapter = DOHAdapter()
            except Exception as _init_exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                self._result.doh_terminal_stage = "dependency_missing"
                self._result.doh_provider_errors = (f"doh_adapter_init_failed:{type(_init_exc).__name__}:{_init_exc}",)
                nonfeed_prelude_accepted["DOH"] = 0
                return ("DOH", 0)
        _doh_session = None
        try:
            from hledac.universal.transport.session_pool import session_pool

            _doh_session = await session_pool.httpx()
            self._result.doh_domains_attempted = 1
            _doh_findings = await self._doh_adapter.run(domain=str(_doh_query), session=_doh_session)
            self._result.doh_request_attempted = True
            _cache_used = getattr(self._doh_adapter, "_cache", None) and str(_doh_query) in self._doh_adapter._cache
            self._result.doh_cache_used = bool(_cache_used)
            if _doh_findings:
                _doh_cands, _doh_rejs, _doh_tel = doh_results_to_findings(
                    _doh_findings, None, query, f"prelude-doh-{int(time_module.time())}"
                )
                nonfeed_prelude_attempted.append("DOH")
                nonfeed_prelude_terminal.append("DOH")
                self._result.doh_raw_count = _doh_tel.get("doh_total", len(_doh_findings))
                _doh_acc = 0
                if _doh_cands and duckdb_store and hasattr(duckdb_store, "async_ingest_findings_batch"):
                    try:
                        if not self._enqueue_duckdb_write(duckdb_store, list(_doh_cands), self.sprint_id or ""):
                            _t = safe_create_task(
                                self._gate_then_ingest_and_accumulate(
                                    duckdb_store, list(_doh_cands), sprint_id=self.sprint_id or ""
                                )
                            )
                            self._bg_tasks.add(_t)
                            _t.add_done_callback(self._bg_tasks.discard)
                    except Exception:  # noqa: BLE001 — best-effort; callback handler; non-critical
                        pass
                self._result.lane_doh_accepted_findings = _doh_acc
                self._result.doh_accepted_findings = _doh_acc
                if _doh_acc > 0:
                    self._result.doh_terminal_stage = "attempted_accepted"
                else:
                    self._result.doh_terminal_stage = "attempted_empty"
                nonfeed_prelude_accepted["DOH"] = _doh_acc
                return ("DOH", _doh_acc)
            else:
                self._result.doh_raw_count = 0
                self._result.doh_terminal_stage = "attempted_empty"
                nonfeed_prelude_accepted["DOH"] = 0
                return ("DOH", 0)
        except TimeoutError:
            self._result.doh_terminal_stage = "timeout"
            self._result.doh_provider_errors = ("doh_timeout",)
            nonfeed_prelude_accepted["DOH"] = 0
            return ("DOH", 0)
        except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            self._result.doh_terminal_stage = "provider_error"
            self._result.doh_provider_errors = (f"{type(_exc).__name__}:{_exc}",)
            nonfeed_prelude_accepted["DOH"] = 0
            return ("DOH", 0)
        finally:
            if _doh_session:
                await _doh_session.close()

    async def _ensure_mandatory_nonfeed_before_return(self, query: str, duckdb_store: Any, reason: str) -> bool:
        """

        Sprint F207T-A: Ensure mandatory nonfeed lanes have terminal state before

        the scheduler can return a meaningful result for a domain query.



        This is the return-path analog of the pre-windup barrier -- it prevents

        the scheduler from returning ACTIVE-phase results when PUBLIC/CT have

        not yet been attempted (even if the windup guard was never reached).



        Rules:

          - domain query + ok/warn memory: both PUBLIC and CT must have terminal state

          - domain query + critical/emergency: may skip with explicit reason recorded

          - non-domain: only PUBLIC required (CT skips with no_domain)

          - Feed-only result: may return if domain query but PUBLIC+CT already terminal



        Semantics:

          - Returns True if the scheduler MAY return (all required lanes terminal)

          - Returns False if return must be DELAYED (required lanes not terminal)

          - On False: sets return_guard telemetry and continues loop if possible



        Args:

            query: Sprint query

            duckdb_store: DuckDB store (may be None)

            reason: Human-readable reason for the return check (e.g. "stop_requested",

                    "max_cycles", "stop_on_first_accepted", "post_sleep_windup")



        Returns:

            True if return is allowed, False if blocked

        """
        self._result.return_guard_checked = True
        _uma = "ok"
        if self._governor is not None:
            try:
                _snap = await self._governor.evaluate()
                _uma = getattr(_snap, "uma_state", "ok")
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        _memory_state = _uma if _uma in ("ok", "warn", "critical", "emergency") else "ok"
        _memory_critical = _memory_state in ("critical", "emergency")
        _is_domain = False
        if self._acquisition_plan is not None:
            _debug = getattr(self._acquisition_plan, "nonfeed_plan_debug", None)
            if _debug is not None:
                _is_domain = getattr(_debug, "domain_detected", False)
        if not _is_domain:
            if getattr(self._config, "acquisition_profile", None) == "nonfeed_diagnostic":
                self._result.return_guard_satisfied = True
                self._result.return_guard_block_reason = ""
                return True
            _pub_discovered = self._result.public_stage_counters.get("discovered_urls", 0)
            _pub_error = getattr(self._result, "public_error", None)
            _pub_timed_out = _pub_error == "terminal:remaining_too_low"
            if _pub_discovered > 0 and self._result.accepted_findings > 0:
                self._result.return_guard_satisfied = True
                self._result.return_guard_block_reason = ""
                return True
            elif _pub_timed_out and self._result.accepted_findings > 0:
                self._result.return_guard_satisfied = True
                self._result.return_guard_block_reason = ""
                return True
            else:
                logger.warning(
                    "[RETURN_GUARD] Non-domain query, no accepted findings (%d) — holding sprint open",
                    self._result.accepted_findings,
                )
                self._result.return_guard_satisfied = False
                self._result.return_guard_block_reason = "non_domain_no_accepted_findings"
                return False
        uma_state = _memory_state
        swap_detected = False
        if self._governor is not None:
            try:
                _snap = await self._governor.evaluate()
                uma_state = getattr(_snap, "uma_state", _memory_state)
                swap_detected = getattr(_snap, "swap_detected", False)
            except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
                pass
        _mlt_required = required_terminal_lanes(
            snapshot=self._acquisition_plan, query=query, uma_state=uma_state, swap_detected=swap_detected
        )
        _required = [mlt.lane.lower() for mlt in _mlt_required if mlt.required]
        self._result.return_guard_required_lanes = tuple(_required)
        _ct_done = (
            self._result.ct_log_discovered > 0
            or self._result.lane_ct_accepted_findings > 0
            or self._result.ct_request_timeout
            or (
                self._result.ct_terminal_stage
                in ("error", "skipped", "request_timeout", "no_candidates", "provider_cooldown", "provider_unavailable")
            )
        )
        _public_done = self._public_outcome is not None
        _observed: dict[str, dict] = {}
        if _public_done and self._public_outcome is not None:
            _observed["PUBLIC"] = self._public_outcome
        if _ct_done:
            _observed["CT"] = {"attempted": True, "skipped": False, "error": None, "timeout": False, "lane": "CT"}
        _unsatisfied: list[str] = []
        for lane in _required:
            if lane == "public" and (not _public_done):
                _unsatisfied.append("public")
            elif lane == "ct" and (not _ct_done):
                _unsatisfied.append("ct")
        if not _unsatisfied:
            if self._result.accepted_findings > 0:
                self._result.return_guard_satisfied = True
                self._result.return_guard_block_reason = ""
                return True
            else:
                logger.warning(
                    "[RETURN_GUARD] Domain query, all lanes terminal but zero accepted findings (%d) — holding sprint open",
                    self._result.accepted_findings,
                )
                self._result.return_guard_satisfied = False
                self._result.return_guard_block_reason = "domain_no_accepted_findings"
                return False
        _attempted: list[str] = []
        _skipped: dict[str, str] = {}
        _errors: dict[str, str] = {}

        async def _try_public():
            if "public" not in _unsatisfied:
                return None
            try:
                return ("public", await self._attempt_public_prewindup_barrier(query))
            except Exception as exc:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                return ("public", exc)

        async def _try_ct():
            if "ct" not in _unsatisfied:
                return None
            try:
                return ("ct", await self._attempt_ct_prewindup_barrier(query))
            except Exception as exc:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                return ("ct", exc)

        _to_try = [_f for _f in (_try_public(), _try_ct()) if _f is not None]
        if _to_try:
            _results = await safe_gather_ok(*_to_try, label="sprint_scheduler:_ensure_mandatory_nonfeed_return_guard")
            for _r in _results:
                if _r is None:
                    continue
                if isinstance(_r, BaseException):
                    if isinstance(_r, asyncio.CancelledError):
                        raise _r
                    log.warning("return_guard gather exception: %s", _r)
                    continue
                _lane, _val = _r
                if _lane == "public":
                    if isinstance(_val, Exception):
                        _skipped["public"] = f"exception:{type(_val).__name__}"
                        _errors["public"] = f"{type(_val).__name__}:{_val}"
                    elif _val is None:
                        _skipped["public"] = "adapter_error"
                        _errors["public"] = "return_guard_public_adapter_error"
                    elif _val.get("error"):
                        _errors["public"] = _val["error"]
                        _attempted.append("public")
                    elif _val.get("timeout"):
                        _skipped["public"] = "timeout"
                        _attempted.append("public")
                    else:
                        _attempted.append("public")
                elif _lane == "ct":
                    if isinstance(_val, Exception):
                        _skipped["ct"] = f"exception:{type(_val).__name__}"
                        _errors["ct"] = f"{type(_val).__name__}:{_val}"
                    elif _val is None:
                        _skipped["ct"] = "adapter_error"
                        _errors["ct"] = "return_guard_ct_adapter_error"
                    elif _val.get("timeout"):
                        _skipped["ct"] = "timeout"
                        _attempted.append("ct")
                    elif _val.get("error"):
                        _errors["ct"] = _val["error"]
                        _attempted.append("ct")
                    else:
                        _attempted.append("ct")
                        if _val.get("raw_count", 0) > 0:
                            self._result.ct_bridge_invoked = False
                            self._result.ct_loss_stage = CTLossStage.RAW_NOT_BRIDGED.value
                            self._result.ct_raw_count = _val.get("raw_count", 0)
                            self._result.ct_raw_sample_count = 0
                            self._result.ct_candidates_built = 0
                            self._result.ct_candidates_accumulated = 0
                            self._result.ct_candidates_stored = 0
                            self._result.ct_storage_rejected = 0
                            self._result.ct_quarantine_count = 0
                            self._result.ct_bridge_rejections_count = 0
                            self._result.ct_bridge_rejection_reasons = ()
        _public_done = self._public_outcome is not None
        _ct_done = (
            self._result.ct_log_discovered > 0
            or self._result.lane_ct_accepted_findings > 0
            or self._result.ct_request_timeout
            or (
                self._result.ct_terminal_stage
                in ("error", "skipped", "request_timeout", "no_candidates", "provider_cooldown", "provider_unavailable")
            )
        )
        _still_unsatisfied: list[str] = []
        for lane in _required:
            if lane == "public" and (not _public_done):
                _still_unsatisfied.append("public")
            elif lane == "ct" and (not _ct_done):
                _still_unsatisfied.append("ct")
        if _still_unsatisfied:
            self._result.return_guard_delayed_for_nonfeed = True
            self._result.return_guard_block_reason = f"nonfeed_not_terminal:{','.join(_still_unsatisfied)}"
            self._result.return_guard_attempted_lanes = tuple(_attempted)
            self._result.return_guard_skipped_lanes = dict(_skipped)
            self._result.return_guard_errors = dict(_errors)
            return False
        self._result.return_guard_satisfied = True
        self._result.return_guard_block_reason = ""
        self._result.return_guard_attempted_lanes = tuple(_attempted)
        self._result.return_guard_skipped_lanes = dict(_skipped)
        self._result.return_guard_errors = dict(_errors)
        return True

    def _check_prewindup_barrier_sync(self, query: str, duckdb_store: Any) -> bool:
        """

        Sprint F207R-A: Synchronous pre-windup barrier check (read-only).

        P1-B FIX: This function is called from windup_guard() callback AFTER

        _ensure_pre_windup_lane_terminal_states() has already run at line 7397.

        Previously this function re-ran _ensure_pre_windup_lane_terminal_states()

        via run_coroutine_threadsafe, causing a RACE CONDITION where both calls

        wrote to self._result fields simultaneously, resulting in:

          - attempted_lanes=[] (second call overwrote first)

          - satisfied=False (skipped lanes not counted correctly)

          - windup_guard_last_allowed=False (callback saw unsatisfied barrier)

        FIX: Read prewindup barrier state directly from self._result instead

        of re-running the async barrier check. This is the correct design because:

          1. _ensure_pre_windup_lane_terminal_states() already ran at line 7397

          2. It set prewindup_barrier_checked=True and populated all barrier fields

          3. windup_guard() is called AFTER step 1, so telemetry is already available

        Returns True if windup is allowed (barrier satisfied or not required).

        Returns False if windup must be blocked (required lanes not terminal).

        Fail-closed: on error, blocks windup with explicit telemetry.
        """
        if not getattr(self._result, "prewindup_barrier_checked", False):
            self._result.prewindup_guard_fail_closed = True
            log.debug("[P1-B] prewindup barrier not checked yet (blocking windup)")
            return False
        satisfied = getattr(self._result, "prewindup_barrier_satisfied", False)
        required_lanes = getattr(self._result, "prewindup_barrier_required_lanes", ())
        attempted_lanes = getattr(self._result, "prewindup_barrier_attempted_lanes", ())
        skipped_lanes = getattr(self._result, "prewindup_barrier_skipped_lanes", {})
        barrier_errors = getattr(self._result, "prewindup_barrier_errors", {})
        self._result.windup_guard_last_reason = "barrier_satisfied" if satisfied else "barrier_blocked"
        if required_lanes and (not satisfied):
            self._result.windup_delayed_for_nonfeed = True
            log.debug(
                "[P1-B] Windup blocked: required=%s satisfied=%s attempted=%s skipped=%s errors=%s",
                required_lanes,
                satisfied,
                attempted_lanes,
                skipped_lanes,
                barrier_errors,
            )
            return False
        return True

    async def _run_one_cycle(
        self,
        lifecycle,
        sources: Sequence[str],
        now_monotonic: float | None = None,
        query: str = "",
        duckdb_store: Any = None,
    ) -> bool:
        await self._ensure_dedup_loaded()
        "\n        Run one bounded fetch cycle across all sources, tier-ordered.\n\n        In aggressive mode, feed/public/CT branches run concurrently with per-branch timeouts.\n\n        Returns False when lifecycle says stop; True otherwise.\n\n        Sprint F228G: when no work items are available (e.g. all sources filtered\n\n        out by prune mode), increments result.consecutive_empty_cycles and\n\n        returns True so the loop can decide whether to force windup. Resets\n\n        the counter when real work is performed.\n\n        "
        work_items = self._build_work_items(sources)
        current_cycle = self._result.cycles_started
        work_items = self._sort_work_items_by_economics(work_items, current_cycle)
        if not work_items:
            self._result.consecutive_empty_cycles += 1
            if self._result.consecutive_empty_cycles > self._result.max_consecutive_empty_cycles:
                self._result.max_consecutive_empty_cycles = self._result.consecutive_empty_cycles
            log.debug(
                "[P1-1] Empty work_items cycle %d (consecutive=%d)",
                current_cycle + 1,
                self._result.consecutive_empty_cycles,
            )
            try:
                _elapsed = _time.monotonic() - self._wall_clock_start
                await check_zero_findings_alert(
                    elapsed_s=_elapsed,
                    consecutive_empty_cycles=self._result.consecutive_empty_cycles,
                    total_findings=self._result.accepted_findings,
                )
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
            return True
        self._result.cycles_started += 1
        _lc_adapter = getattr(self, "_lc_adapter", None)
        if _lc_adapter is not None:
            _lc_adapter.set_first_cycle_ran()
        elif hasattr(lifecycle, "first_cycle_ran"):
            lifecycle.first_cycle_ran = True
        self._result.consecutive_empty_cycles = 0
        if self._config.aggressive_mode:
            return await self._run_one_cycle_aggressive(lifecycle, work_items, query, duckdb_store)
        else:
            return await self._run_one_cycle_stable(lifecycle, work_items, query, duckdb_store)

    async def _run_one_cycle_stable(self, lifecycle, work_items: list, query: str, duckdb_store: Any) -> bool:
        """

        Stable mode: feed sources run first, then public discovery runs after.

        CT discovery runs once after the main cycle loop (in __main__.py).



        F212-B: Public discovery runs under remaining-time-aware asyncio.timeout.

        Branch is skipped if remaining time is at or below the safety floor.



        # P1.5-fix 2026-06-07: initialize _seed_ctx at function-top so it
        # is defined for the ENTIRE body of _run_one_cycle_stable, including
        # the public-outcome assembly at line ~15535 ("seed_context_available"
        # telemetry). The previous try-block-scoped initialization (14443)
        # was insufficient because the public-outcome code is OUTSIDE the
        # try block. When the nonfeed prelude never assigns _seed_ctx
        # (e.g. no pivot seeds and no next_seeds_ioc), NameError was raised
        # after the public branch completed.
        """
        _query_domain_candidates: list[str] = []
        if query and isinstance(query, str) and query.strip():
            _candidates = extract_domain_candidates_from_text(
                query, source_url=None, source_family="query", min_confidence=0.3
            )
            _query_domain_candidates = [c.domain for c in _candidates if c.confidence >= 0.3]
        _synthetic_domains: list[str] = []
        _bootstrap_enabled: bool = False
        if self._acquisition_plan is not None:
            _bootstrap_enabled = getattr(self._acquisition_plan, "bootstrap_enabled", False)
        else:
            _acq_profile = getattr(self._config, "acquisition_profile", "") or ""
            _bootstrap_opt_out_profiles = frozenset({"nonfeed_diagnostic", "none", "off"})
            _bootstrap_enabled = _acq_profile not in _bootstrap_opt_out_profiles
        async_run_live_feed, FeedPipelineRunResult = _import_live_feed_pipeline()
        _wall_elapsed = _time.monotonic() - self._wall_clock_start
        logger.debug(
            "[PUBLIC_BRANCH_ENTRY] wall_elapsed=%.2f sprint_duration=%.2f windup_lead=%.2f remaining_s=%.2f",
            _wall_elapsed,
            self._config.sprint_duration_s,
            self._config.effective_windup_lead_s,
            lifecycle.remaining_time(),
        )
        remaining_s = lifecycle.remaining_time()
        _nonfeed_terminal = bool(
            self._result.lane_ct_accepted_findings > 0
            or self._result.lane_wayback_accepted_findings > 0
            or self._result.lane_pdns_accepted_findings > 0
            or (self._result.lane_blockchain_accepted_findings > 0)
            or (self._result.lane_ipfs_accepted_findings > 0)
            or (self._result.lane_doh_accepted_findings > 0)
        )
        semaphore = asyncio.Semaphore(self._config.max_parallel_sources)

        async def fetch_one(work) -> tuple[str, FeedPipelineRunResult]:
            async with semaphore:
                should_fetch, budget_reason = self._feed_dominance_should_fetch(work, _nonfeed_terminal)
                if not should_fetch:
                    log.debug(
                        "[F216E] Feed source skipped by budget cap: url=%s reason=%s", work.feed_url, budget_reason
                    )
                    return (
                        work.feed_url,
                        FeedPipelineRunResult(
                            feed_url=work.feed_url,
                            fetched_entries=0,
                            accepted_findings=0,
                            stored_findings=0,
                            patterns_configured=0,
                            matched_patterns=0,
                            pages=(),
                            error="feed_budget_cap_suppressed",
                        ),
                    )
                try:
                    async with asyncio.timeout(30.0):
                        from hledac.universal.pipeline.live_feed_pipeline import FeedIngestContext

                        result = await async_run_live_feed(
                            feed_url=work.feed_url,
                            max_entries=work.max_entries,
                            sprint_id=self.sprint_id or "",
                            store=duckdb_store,
                            ingest_ctx=FeedIngestContext(
                                privacy_layer=self._privacy_layer,
                                evidence_log=self._evidence_log,
                                graph_accumulator=self._graph_accumulator,
                                temporal_predictor=self._temporal_predictor,
                                layer_manager=getattr(self, "_layer_manager", None),
                            )
                            if duckdb_store is not None
                            else None,
                        )
                    return (work.feed_url, result)
                except TimeoutError:
                    return (
                        work.feed_url,
                        FeedPipelineRunResult(
                            feed_url=work.feed_url,
                            fetched_entries=0,
                            accepted_findings=0,
                            stored_findings=0,
                            patterns_configured=0,
                            matched_patterns=0,
                            pages=(),
                            error="timeout",
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    return (
                        work.feed_url,
                        FeedPipelineRunResult(
                            feed_url=work.feed_url,
                            fetched_entries=0,
                            accepted_findings=0,
                            stored_findings=0,
                            patterns_configured=0,
                            matched_patterns=0,
                            pages=(),
                            error=f"exception:{type(exc).__name__}:{exc}",
                        ),
                    )

        _initial_seed_ctx = None
        if _query_domain_candidates:
            _initial_seed_ctx = NonfeedSeedContext(domains=tuple(_query_domain_candidates[:10]))

        async def _run_feed_branch() -> None:
            """Feed branch: fetches all sources concurrently."""
            nonlocal work_items
            async_run_live_feed, FeedPipelineRunResult = _import_live_feed_pipeline()
            branch_concurrency = 4
            if self._governor is not None:
                try:
                    decision = await self._governor.evaluate()
                    branch_concurrency = decision.branch_concurrency
                    if decision.uma_state in ("critical", "emergency"):
                        self._result.pressure_violations += 1
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            semaphore = asyncio.Semaphore(min(branch_concurrency, self._config.max_parallel_sources))

            async def fetch_one(work) -> tuple[str, FeedPipelineRunResult]:
                should_fetch, budget_reason = self._feed_dominance_should_fetch(work, _nonfeed_terminal)
                if not should_fetch:
                    return (
                        work.feed_url,
                        FeedPipelineRunResult(
                            feed_url=work.feed_url,
                            fetched_entries=0,
                            accepted_findings=0,
                            stored_findings=0,
                            patterns_configured=0,
                            matched_patterns=0,
                            pages=(),
                            error="feed_budget_cap_suppressed",
                        ),
                    )
                async with semaphore:
                    try:
                        async with asyncio.timeout(30.0):
                            result = await async_run_live_feed(
                                feed_url=work.feed_url,
                                max_entries=work.max_entries,
                                sprint_id=self.sprint_id or "",
                                store=duckdb_store,
                                ingest_ctx=FeedIngestContext(
                                    privacy_layer=self._privacy_layer,
                                    evidence_log=self._evidence_log,
                                    graph_accumulator=self._graph_accumulator,
                                    temporal_predictor=self._temporal_predictor,
                                    layer_manager=getattr(self, "_layer_manager", None),
                                )
                                if duckdb_store is not None
                                else None,
                            )
                        return (work.feed_url, result)
                    except TimeoutError:
                        return (
                            work.feed_url,
                            FeedPipelineRunResult(
                                feed_url=work.feed_url,
                                fetched_entries=0,
                                accepted_findings=0,
                                stored_findings=0,
                                patterns_configured=0,
                                matched_patterns=0,
                                pages=(),
                                error="timeout",
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        return (
                            work.feed_url,
                            FeedPipelineRunResult(
                                feed_url=work.feed_url,
                                fetched_entries=0,
                                accepted_findings=0,
                                stored_findings=0,
                                patterns_configured=0,
                                matched_patterns=0,
                                pages=(),
                                error=f"exception:{type(exc).__name__}:{exc}",
                            ),
                        )

            tasks = [fetch_one(w) for w in work_items]
            results = await safe_gather_ok(*tasks, label="sprint_scheduler:14339")
            for feed_url, result in results:
                self._process_result(feed_url, result)
            if work_items:
                domains = []
                for w in work_items:
                    url = w.feed_url
                    if url and "://" in url:
                        try:
                            from urllib.parse import urlparse

                            netloc = urlparse(url).netloc
                            if netloc:
                                domains.append(netloc.split(":")[0])
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
                if domains:
                    unique = list(dict.fromkeys(domains))[:5]
                    _t = safe_create_task(self._speculative_dns_prefetch(unique), name="sprint:dns_prefetch")
                    self._bg_tasks.add(_t)
                    _t.add_done_callback(self._bg_tasks.discard)
            _accepted = sum((getattr(r, "accepted_findings", 0) or 0 for _, r in results))
            if _accepted:
                _log = get_logger(__name__)
                _log.debug("[F205C] Feed accepted findings not in scope for sidecar dispatch. accepted=%d", _accepted)

        async def _run_public_branch() -> None:
            """Public discovery branch — runs concurrently with FEED branch."""
            branch_timeout = self._branch_timeout_s("PUBLIC", remaining_s)
            if branch_timeout > 0:
                self._lane_budget_pool.allocate("PUBLIC", branch_timeout)
            if branch_timeout <= 0:
                log.debug("[F212-B] PUBLIC branch skipped: remaining=%.1fs", remaining_s)
                self._result.public_branch_timed_out = True
                self._result.branch_timeout_count += 1
                self._result.branch_skipped_remaining_too_low += 1
                self._result.public_error = "terminal:remaining_too_low"
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": True,
                    "skip_reason": "terminal:remaining_too_low",
                    "raw_count": 0,
                    "built_count": 0,
                    "accepted_count": 0,
                    "error": "terminal:remaining_too_low",
                    "timeout": True,
                    "duration_s": None,
                }
                return
            try:
                _seed_ctx = None
                async with asyncio.timeout(branch_timeout):
                    self._public_bootstrap_enabled_at_timeout = _bootstrap_enabled
                    await self._run_public_discovery_in_cycle(
                        query=query,
                        duckdb_store=duckdb_store,
                        hermes_engine=self._hermes_engine,
                        memory_manager=self._memory_manager,
                        public_bootstrap_enabled=_bootstrap_enabled,
                        seed_context=_initial_seed_ctx,
                    )
            except TimeoutError:
                log.debug("[stable] PUBLIC branch timed out after %ss", branch_timeout)
                self._result.public_branch_timed_out = True
                self._result.branch_timeout_count += 1
                self._result.public_error = "terminal:timeout"
                self._emit_source_family_event(family="PUBLIC", event="timeout", reason="terminal:timeout")
                self._notify_governor_branch_timeout()
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": self._result.public_discovered,
                    "built_count": 0,
                    "accepted_count": 0,
                    "error": "terminal:timeout",
                    "timeout": True,
                    "duration_s": None,
                }
            except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.debug("[stable] PUBLIC branch error: %s", exc)
                self._result.public_error = f"{type(exc).__name__}:{exc}"
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": self._result.public_discovered,
                    "built_count": 0,
                    "accepted_count": 0,
                    "error": str(exc),
                    "timeout": False,
                    "duration_s": None,
                }

        async def _run_advisory_branch() -> None:
            """Advisory lanes branch — runs concurrently with FEED and PUBLIC."""
            lanes_timeout = self._branch_timeout_s("ADVISORY", remaining_s)
            if lanes_timeout <= 0:
                log.debug("[F212-B] ADVISORY lanes skipped: remaining=%.1fs", remaining_s)
                return
            if self._governor is not None:
                _lane_check = self._governor.lane_admission("advisory", risk_level="high")
                _uma = _lane_check.uma_state
            else:
                _uma = getattr(self._governor, "_uma_state", "ok") if self._governor else "ok"
            try:
                async with asyncio.timeout(lanes_timeout):
                    _seed_ctx = None
                    _pivot_has_seeds = (
                        self._result.pivot_seed_domains or self._result.pivot_seed_ips or self._result.pivot_seed_urls
                    )
                    _next_seeds_has_iocs = (
                        self._result.next_seeds_ioc_domains
                        or self._result.next_seeds_ioc_ips
                        or self._result.next_seeds_ioc_urls
                        or self._result.next_seeds_ioc_hashes
                    )
                    if _pivot_has_seeds or _next_seeds_has_iocs:
                        _seed_ctx = NonfeedSeedContext(
                            domains=tuple(
                                _query_domain_candidates
                                + (self._result.pivot_seed_domains or ())
                                + (self._result.next_seeds_ioc_domains or ())
                            ),
                            ips=tuple((self._result.pivot_seed_ips or ()) + (self._result.next_seeds_ioc_ips or ())),
                            urls=tuple((self._result.pivot_seed_urls or ()) + (self._result.next_seeds_ioc_urls or ())),
                            hashes=tuple(
                                (self._result.pivot_seed_hashes or ()) + (self._result.next_seeds_ioc_hashes or ())
                            ),
                            cves=tuple((self._result.pivot_seed_cves or ()) + (self._result.next_seeds_ioc_cves or ())),
                        )
                    if self._graph_accumulator is None:
                        self._graph_accumulator = SprintGraphAccumulator()
                    _clearnet_max = 4
                    if self._governor is not None:
                        try:
                            _gov_decision = await self._governor.evaluate()
                            _clearnet_max = _gov_decision.fetch_limit
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
                    _streaming_outcomes: list = []
                    async for _outcome_batch in run_enabled_acquisition_lanes_streaming(
                        snapshot=self._acquisition_plan if self._acquisition_plan is not None else None,
                        query=query,
                        store=duckdb_store,
                        uma_state=_uma,
                        clearnet_max=_clearnet_max,
                        seed_context=_seed_ctx,
                        graph_accumulator=self._graph_accumulator,
                        min_finished=0,
                        on_lane_complete=None,
                    ):
                        _streaming_outcomes = list(_outcome_batch)
                        _total_accepted = sum(
                            (
                                getattr(_o, "accepted_findings", 0) or 0
                                for _o in _streaming_outcomes
                                if getattr(_o, "attempted", False)
                            )
                        )
                        if _total_accepted >= 3:
                            break
                    _outcomes = tuple(_streaming_outcomes) if _streaming_outcomes else None
                    if _outcomes:
                        self._lane_outcomes = _outcomes
                        self._result.acquisition_lane_outcomes = _outcomes
                        self._accumulate_lane_findings(_outcomes, query)
                        await self._ingest_ct_lane_candidates(_outcomes, duckdb_store)
                        self._record_quality_rejections_from_store(duckdb_store)
                        ct_findings: list = []
                        for _oc in _outcomes:
                            if getattr(_oc, "source_family", None) == "ct":
                                _cands = getattr(_oc, "candidate_findings", ()) or ()
                                if _cands:
                                    ct_findings.extend(_cands)
                        if ct_findings:
                            await self._run_ct_to_passivedns_active_pivot(
                                ct_findings=ct_findings, duckdb_store=duckdb_store, remaining_s=remaining_s
                            )
                        for _rec in self._result.quality_rejection_ledger or ():
                            try:
                                self._nonfeed_ledger.add_quality_rejection(
                                    source_family=_rec.source_family or "unknown",
                                    reason=_rec.reason or "unknown",
                                    sample_url=getattr(_rec, "url_sample", "") or "",
                                    sample_value=getattr(_rec, "finding_id", "")[:16],
                                )
                            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                pass
                        self._ingest_feed_public_candidates_to_ledger()
            except TimeoutError:
                log.debug("[stable] ADVISORY lanes timed out after %ss", lanes_timeout)
            except asyncio.CancelledError:
                raise
            except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                _log_advisory_dedup(
                    log,
                    f"stable_advisory_lane_fail:{type(_exc).__name__}",
                    "[stable] ADVISORY lane runner exception: %s: %s",
                    type(_exc).__name__,
                    _exc,
                )

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_run_feed_branch(), name="sprint:feed_branch", eager_start=True)
                tg.create_task(_run_public_branch(), name="sprint:public_branch", eager_start=True)
                tg.create_task(_run_advisory_branch(), name="sprint:advisory_branch", eager_start=True)
        except* Exception as e:
            log.debug("[stable] Branch TaskGroup failed: %s", e)
        try:
            from hledac.universal.core.memory_cycle import gc_cycle_maintain

            gc_cycle_maintain(force=False)
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        try:
            self._maybe_call_pressure_relief()
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        return True

    def _min_branch_remaining_s(self, remaining_s: float | None = None) -> float:
        """
        F273B: Dynamic branch-remaining safety floor based on remaining time.

        Returns the safety floor (in seconds) below which a branch is skipped
        with `terminal:remaining_too_low`. Replaces the cycle-ema-based formula
        (0.2 * cycle_ema) that was too low for 300s+ sprints where 25 cycles
        over-commit the timeline.

        Formula (always-on, bounded [2.0, 5.0]):
          remaining_s = time left in sprint (passed as argument)
          base = max(2.0, 0.15 * remaining_s)
          return min(5.0, base)

        Examples (300s sprint):
          - remaining_s=150s (50% left) -> base = max(2.0, 22.5) = 22.5 -> return 5.0s (capped)
          - remaining_s=90s  (30% left) -> base = max(2.0, 13.5) = 13.5 -> return 5.0s (capped)
          - remaining_s=60s  (20% left) -> base = max(2.0, 9.0) = 9.0  -> return 5.0s (capped)
          - remaining_s=33.3s(11% left) -> base = max(2.0, 5.0) = 5.0  -> return 5.0s (at breakpoint)
          - remaining_s=30s  (10% left) -> base = max(2.0, 4.5) = 4.5  -> return 4.5s
          - remaining_s=15s  (5% left)  -> base = max(2.0, 2.25) = 2.25 -> return 2.25s

        Why 0.15 * remaining_s: floor scales with remaining time so branches
        get adequate time in long sprints while staying low in short sprints.
        The 5.0s cap is active when 0.15*remaining_s > 5.0, i.e. remaining_s > 33.3s.
        This prevents 300s sprints from losing all branches to terminal:remaining_too_low.

        Fail-safe: if remaining_s is None or <= 0, falls back to cycle-ema-based
        formula (0.1 * cycle_ema, bounded [2.0, 5.0]) for backward compatibility.
        """
        if remaining_s is None or remaining_s <= 0.0:
            cycle_ema = float(getattr(self, "_cycle_time_ema", 0.0) or 0.0)
            if cycle_ema <= 0.0:
                return self._config._MIN_BRANCH_REMAINING_S_DEFAULT
            base = max(self._config._MIN_BRANCH_REMAINING_S_DEFAULT, 0.1 * cycle_ema)
            return float(min(self._config._MIN_BRANCH_REMAINING_S_CAP, base))
        base = max(self._config._MIN_BRANCH_REMAINING_S_DEFAULT, 0.15 * remaining_s)
        return float(min(self._config._MIN_BRANCH_REMAINING_S_CAP, base))

    async def _drain_pending_pattern_extractions(self, remaining_s: float) -> None:
        """
        F273C + F273H: Pre-windup drain of in-flight pattern-extraction Futures.

        Calls into `public_fetcher.drain_pending_extractions(deadline_s)` to
        await any HTML the public fetcher has already submitted to
        CPU_EXECUTOR. This is the direct fix for the "16/16 fetched → 0
        matched patterns → 0 stored" failure mode where the windup transition
        cancelled the awaiting branch before its extraction Future resolved.

        F273H: Adaptive drain deadline. Before this fix the drain deadline was
        a fixed 30s that could exceed the remaining sprint time, causing the
        drain itself to consume nearly the entire active window on short
        sprints (windup = 304.57s of 305s observed). Now bounded to
        min(30s, remaining_s * 0.3) so the drain never consumes more than 30%
        of whatever time remains. Also early-exits when the drain registry
        is already empty, avoiding a pointless wait.

        Always-on, bounded (adaptive deadline), fail-soft: any error
        in the drain path returns silently and the windup decision proceeds.

        Telemetry recorded on self._result:
          - pattern_extraction_drain_completed  (cumulative count)
          - pattern_extraction_drain_timed_out  (cumulative count)
          - pattern_extraction_drain_elapsed_s  (last drain wall-clock)
          - effective_windup_lead_used_s  (actual windup lead applied)
        """
        import time as _t_f273c_drain

        _t0 = _t_f273c_drain.monotonic()
        try:
            from hledac.universal.fetching.public_fetcher import drain_pending_extractions, get_drain_stats
        except Exception as exc:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
            log.debug("[F273C] Could not import drain helpers: %s", exc)
            return
        pre = get_drain_stats()
        if pre["registry_size"] == 0:
            self._result.pattern_extraction_drain_completed += 0
            self._result.pattern_extraction_drain_timed_out += 0
            self._result.pattern_extraction_drain_elapsed_s = 0.0
            return
        _adaptive_deadline_s = min(30.0, max(0.1, remaining_s * 0.3))
        completed, timed_out, _elapsed = await drain_pending_extractions(deadline_s=_adaptive_deadline_s)
        post = get_drain_stats()
        self._result.pattern_extraction_drain_completed += completed
        self._result.pattern_extraction_drain_timed_out += timed_out
        self._result.pattern_extraction_drain_elapsed_s = round(_t_f273c_drain.monotonic() - _t0, 4)
        if completed or timed_out:
            log.debug(
                "[F273C] pattern-extraction drain: completed=%d timed_out=%d elapsed=%.2fs adaptive_deadline=%.2fs registry_pre=%d registry_post=%d",
                completed,
                timed_out,
                _elapsed,
                _adaptive_deadline_s,
                pre["registry_size"],
                post["registry_size"],
            )

    def _maybe_call_pressure_relief(self) -> None:
        """
        F273G: Per-sprint macOS malloc pressure relief.

        Calls the existing ``core.memory_cycle.malloc_zone_pressure_relief()``
        helper to ask the Darwin allocator to release fragmented pages. Cheap
        (single ctypes syscall), thread-safe in libmalloc, and fail-soft on
        non-Darwin / on ctypes errors.

        Wired into the pre-windup barrier so the windup phase starts with a
        clean allocator state — better DuckDB ingest + LMDB mmap behavior +
        reduced RSS fragmentation for the Hermes load that may follow.

        Telemetry recorded on self._result:
          - malloc_pressure_relief_count      (cumulative calls)
          - malloc_pressure_relief_last_rc    (last return value, 0 = no-op)
          - malloc_pressure_relief_last_at_s  (wall-clock of last call)

        Bounded: 1 call per windup decision. No new feature flags.
        """
        try:
            from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief as _f273g_relief

            rc = _f273g_relief()
        except Exception as exc:  # noqa: BLE001 — best-effort; memory operation; non-critical
            log.debug("[F273G] malloc_zone_pressure_relief unavailable: %s", exc)
            return
        import time as _t_f273g

        self._result.malloc_pressure_relief_count += 1
        self._result.malloc_pressure_relief_last_rc = int(rc)
        self._result.malloc_pressure_relief_last_at_s = round(_t_f273g.monotonic(), 3)
        if rc > 0:
            log.debug("[F273G] malloc pressure relief released %d bytes (rc=%d)", rc, rc)

    def _branch_timeout_s(self, branch_name: str, remaining_s: float) -> float:
        """

        F212-B: Compute remaining-time-aware timeout for a named branch.



        Formula: min(config_timeout, remaining_s * 0.5, MAX_BRANCH_TIMEOUT_CAP)



        - Prevents a branch from consuming more than 50% of remaining cycle time

        - Capped at MAX_BRANCH_TIMEOUT_CAP to bound absolute worst case

        - Returns 0 when remaining_s <= MIN_BRANCH_REMAINING_S (safety floor)

        F273B: Floor is remaining-time-aware via self._min_branch_remaining_s(remaining_s).

        """
        floor = self._min_branch_remaining_s(remaining_s)
        _uma_state = "ok"
        _uma_gib = 0.0
        try:
            from hledac.universal.core.resource_governor import sample_uma_status as _sample_uma

            _uma = _sample_uma()
            _uma_state = getattr(_uma, "state", "ok")
            _uma_gib = getattr(_uma, "system_used_gib", 0.0)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        logger.debug(
            "[BRANCH_TIMEOUT] branch=%s remaining_s=%.2f floor=%.2f uma_state=%s uma_gib=%.2f",
            branch_name,
            remaining_s,
            floor,
            _uma_state,
            _uma_gib,
        )
        if remaining_s < floor:
            logger.debug(
                "[BRANCH_TIMEOUT] branch=%s SKIPPED remaining_s=%.2f < floor=%.2f (uma_state=%s)",
                branch_name,
                remaining_s,
                floor,
                _uma_state,
            )
            return 0.0
        base = (
            self._config.branch_timeout_budget_s
            if self._config.branch_timeout_budget_s > 0
            else self._config.aggressive_branch_timeout_s
        )
        try:
            from hledac.universal.core.resource_governor import sample_uma_status as _sample_uma

            uma = _sample_uma()
            uma_state = getattr(uma, "state", "ok")
            system_used_gib = getattr(uma, "system_used_gib", 0.0)
            if uma_state == "emergency":
                base = min(base, 20.0)
                log.debug(
                    "[F273G+F265H-EXT] %s branch timeout clamped to %.1fs (uma_state=%s system_used_gib=%.2f concurrency=1)",
                    branch_name,
                    base,
                    uma_state,
                    system_used_gib,
                )
            elif uma_state == "critical":
                if system_used_gib >= 6.85:
                    base = min(base, 15.0)
                    log.debug(
                        "[F273G+F265H-EXT] %s branch timeout clamped to %.1fs (uma_state=%s system_used_gib=%.2f concurrency=2)",
                        branch_name,
                        base,
                        uma_state,
                        system_used_gib,
                    )
                else:
                    base = min(base, 20.0)
                    log.debug(
                        "[F273G+F265H-EXT] %s branch timeout clamped to %.1fs (uma_state=%s system_used_gib=%.2f concurrency=3)",
                        branch_name,
                        base,
                        uma_state,
                        system_used_gib,
                    )
            elif uma_state == "warn":
                try:
                    from hledac.universal.core.protocols import get_governor

                    gov = get_governor()
                    snap = gov.snapshot()
                    conc = getattr(snap, "branch_concurrency", 4)
                    if conc <= 2:
                        base = min(base, 25.0)
                    elif conc <= 3:
                        base = min(base, 35.0)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
        except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F273G] could not sample UMA state: %s", _exc)
        bounded = min(base, remaining_s * 0.5, self._config._MAX_BRANCH_TIMEOUT_CAP)
        log.debug(
            "[F212-B] %s branch timeout: base=%.1fs remaining=%.1fs capped=%.1fs",
            branch_name,
            base,
            remaining_s,
            bounded,
        )
        return bounded

    def _notify_governor_branch_timeout(self) -> None:
        """F2-2: Notify governor of branch timeout for EMA tracking."""
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                governor.record_branch_timeout()
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass

    def _notify_governor_branch_success(self) -> None:
        """F2-2: Notify governor of successful branch completion for EMA decay."""
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                governor.record_branch_success()
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass

    async def _run_one_cycle_aggressive(self, lifecycle, work_items: list, query: str, duckdb_store: Any) -> bool:
        """

        Aggressive mode: feed, public discovery, and CT branches fire concurrently.

        Each branch has its own timeout budget; slow branches are cancelled without

        affecting other branches.



        F212-B: All branch timeouts are remaining-time-aware and capped at

        min(config_timeout, remaining * 0.5, MAX_CAP). Branches are skipped with

        terminal outcome when remaining time is below the safety floor.

        """
        import asyncio as asyncio

        _wall_elapsed = _time.monotonic() - self._wall_clock_start
        logger.debug(
            "[PUBLIC_BRANCH_ENTRY:AGGRESSIVE] wall_elapsed=%.2f sprint_duration=%.2f windup_lead=%.2f remaining_s=%.2f",
            _wall_elapsed,
            self._config.sprint_duration_s,
            self._config.effective_windup_lead_s,
            lifecycle.remaining_time(),
        )
        remaining_s = lifecycle.remaining_time()
        floor = self._min_branch_remaining_s(remaining_s)
        if remaining_s < floor:
            log.debug("[F212-B] Aggressive cycle skipped: remaining=%.1fs < floor=%.1fs", remaining_s, floor)
            self._result.public_branch_timed_out = True
            self._result.ct_branch_timed_out = True
            self._result.branch_timeout_count += 2
            self._result.branch_skipped_remaining_too_low += 2
            self._result.public_error = "terminal:remaining_too_low"
            self._result.ct_log_error = "terminal:remaining_too_low"
            self._public_outcome = {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": True,
                "skip_reason": "terminal:remaining_too_low",
                "raw_count": 0,
                "built_count": 0,
                "accepted_count": 0,
                "error": "terminal:remaining_too_low",
                "timeout": True,
                "duration_s": None,
            }
            self._result.ct_terminal_stage = "terminal_by_timeout"
            self._emit_source_family_event(
                family="PUBLIC", event="timeout", reason="terminal:remaining_too_low", terminal_state="terminal"
            )
            self._emit_source_family_event(
                family="CT", event="timeout", reason="terminal:remaining_too_low", terminal_state="terminal"
            )
            self._notify_governor_branch_timeout()
            self._notify_governor_branch_timeout()
            return True
        _nonfeed_terminal = bool(
            self._result.lane_ct_accepted_findings > 0
            or self._result.lane_wayback_accepted_findings > 0
            or self._result.lane_pdns_accepted_findings > 0
            or (self._result.lane_blockchain_accepted_findings > 0)
            or (self._result.lane_ipfs_accepted_findings > 0)
            or (self._result.lane_doh_accepted_findings > 0)
        )

        async def _run_feed_branch() -> None:
            """Feed branch: fetches all sources concurrently."""
            async_run_live_feed, FeedPipelineRunResult = _import_live_feed_pipeline()
            branch_concurrency = 4
            if self._governor is not None:
                try:
                    decision = await self._governor.evaluate()
                    branch_concurrency = decision.branch_concurrency
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            semaphore = asyncio.Semaphore(min(branch_concurrency, self._config.max_parallel_sources))

            async def fetch_one(work) -> tuple[str, FeedPipelineRunResult]:
                should_fetch, budget_reason = self._feed_dominance_should_fetch(work, _nonfeed_terminal)
                if not should_fetch:
                    return (
                        work.feed_url,
                        FeedPipelineRunResult(
                            feed_url=work.feed_url,
                            fetched_entries=0,
                            accepted_findings=0,
                            stored_findings=0,
                            patterns_configured=0,
                            matched_patterns=0,
                            pages=(),
                            error="feed_budget_cap_suppressed",
                        ),
                    )
                async with semaphore:
                    try:
                        async with asyncio.timeout(30.0):
                            result = await async_run_live_feed(
                                feed_url=work.feed_url,
                                max_entries=work.max_entries,
                                sprint_id=self.sprint_id or "",
                                store=duckdb_store,
                                ingest_ctx=FeedIngestContext(
                                    privacy_layer=self._privacy_layer,
                                    evidence_log=self._evidence_log,
                                    graph_accumulator=self._graph_accumulator,
                                    temporal_predictor=self._temporal_predictor,
                                    layer_manager=getattr(self, "_layer_manager", None),
                                )
                                if duckdb_store is not None
                                else None,
                            )
                        return (work.feed_url, result)
                    except TimeoutError:
                        return (
                            work.feed_url,
                            FeedPipelineRunResult(
                                feed_url=work.feed_url,
                                fetched_entries=0,
                                accepted_findings=0,
                                stored_findings=0,
                                patterns_configured=0,
                                matched_patterns=0,
                                pages=(),
                                error="timeout",
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        return (
                            work.feed_url,
                            FeedPipelineRunResult(
                                feed_url=work.feed_url,
                                fetched_entries=0,
                                accepted_findings=0,
                                stored_findings=0,
                                patterns_configured=0,
                                matched_patterns=0,
                                pages=(),
                                error=f"exception:{type(exc).__name__}:{exc}",
                            ),
                        )

            tasks = [fetch_one(w) for w in work_items]
            results: list[tuple[str, FeedPipelineRunResult]] = await safe_gather_ok(
                *tasks, label="sprint_scheduler:14339"
            )
            for feed_url, result in results:
                self._process_result(feed_url, result)
            if work_items:
                domains = []
                for w in work_items:
                    url = w.feed_url
                    if url and "://" in url:
                        try:
                            from urllib.parse import urlparse

                            netloc = urlparse(url).netloc
                            if netloc:
                                domains.append(netloc.split(":")[0])
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
                if domains:
                    unique = list(dict.fromkeys(domains))[:5]
                    _t = safe_create_task(self._speculative_dns_prefetch(unique), name="sprint:dns_prefetch_agg")
                    self._bg_tasks.add(_t)
                    _t.add_done_callback(self._bg_tasks.discard)
            _accepted = sum((getattr(r, "accepted_findings", 0) or 0 for _, r in results))
            if _accepted:
                _log = get_logger(__name__)
                _log.debug(
                    "[F205C] Aggressive feed accepted findings not in scope for sidecar dispatch. accepted=%d",
                    _accepted,
                )

        async def _run_public_branch() -> None:
            """Public discovery branch with remaining-time-aware asyncio.timeout."""
            branch_timeout = self._branch_timeout_s("PUBLIC", remaining_s)
            if branch_timeout > 0:
                self._lane_budget_pool.allocate("PUBLIC", branch_timeout)
            if branch_timeout <= 0:
                log.debug("[F212-B] PUBLIC branch skipped: remaining=%.1fs", remaining_s)
                self._result.branch_skipped_remaining_too_low += 1
                self._result.public_error = "terminal:remaining_too_low"
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": True,
                    "skip_reason": "terminal:remaining_too_low",
                    "raw_count": 0,
                    "built_count": 0,
                    "accepted_count": 0,
                    "error": "terminal:remaining_too_low",
                    "timeout": False,
                    "duration_s": None,
                }
                return
            if self._public_consecutive_timeouts >= 3:
                log.debug("[F26X-E] PUBLIC branch skipped: %d consecutive timeouts", self._public_consecutive_timeouts)
                self._result.public_error = "terminal:circuit_breaker"
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": True,
                    "skip_reason": "terminal:circuit_breaker",
                    "raw_count": 0,
                    "built_count": 0,
                    "accepted_count": 0,
                    "error": "terminal:circuit_breaker",
                    "timeout": False,
                    "duration_s": None,
                }
                return
            try:
                _seed_ctx = None
                async with asyncio.timeout(branch_timeout):
                    _bootstrap_en = getattr(self._acquisition_plan, "bootstrap_enabled", False)
                    if not _bootstrap_en:
                        _acq_profile = getattr(self._config, "acquisition_profile", "") or ""
                        _bootstrap_en = _acq_profile not in frozenset({"nonfeed_diagnostic", "none", "off"})
                    self._public_bootstrap_enabled_at_timeout = _bootstrap_en
                    await self._run_public_discovery_in_cycle(
                        query=query,
                        duckdb_store=duckdb_store,
                        hermes_engine=self._hermes_engine,
                        memory_manager=self._memory_manager,
                        public_bootstrap_enabled=_bootstrap_en,
                        seed_context=_seed_ctx,
                    )
            except TimeoutError:
                log.debug("[aggressive] Public branch timed out after %ss", branch_timeout)
                self._result.public_branch_timed_out = True
                self._result.branch_timeout_count += 1
                self._notify_governor_branch_timeout()
                _existing_outcome = getattr(self, "_public_outcome", None) or {}
                _existing_raw = _existing_outcome.get("raw_count", 0) or 0
                _existing_accepted = _existing_outcome.get("accepted_count", 0) or 0
                _bootstrap_was_enabled = getattr(self, "_public_bootstrap_enabled_at_timeout", _bootstrap_enabled)
                if _bootstrap_was_enabled and _existing_raw > 0:
                    _precise_stage = _PublicStage.BOOTSTRAP_ATTEMPTED_TIMEOUT
                elif _bootstrap_was_enabled and _existing_raw == 0:
                    _precise_stage = _PublicStage.BOOTSTRAP_ZERO_CANDIDATES_TIMEOUT
                else:
                    _precise_stage = "terminal:timeout"
                self._result.public_error = _precise_stage
                self._emit_source_family_event(
                    family="PUBLIC", event="timeout", reason=_precise_stage, terminal_state="terminal"
                )
                self._public_consecutive_timeouts += 1
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": _existing_raw,
                    "built_count": 0,
                    "accepted_count": _existing_accepted,
                    "error": _precise_stage,
                    "timeout": True,
                    "duration_s": branch_timeout,
                }
            except asyncio.CancelledError:
                log.debug("[aggressive] Public branch cancelled")
                raise
            except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.debug("[aggressive] Public branch error: %s", exc)
                self._result.public_error = f"{type(exc).__name__}:{exc}"
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": self._result.public_discovered,
                    "built_count": 0,
                    "accepted_count": self._result.public_accepted_findings,
                    "error": f"{type(exc).__name__}:{exc}",
                    "timeout": False,
                    "duration_s": branch_timeout,
                }
            else:
                self._public_consecutive_timeouts = 0
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": self._result.public_discovered,
                    "built_count": 0,
                    "accepted_count": self._result.public_accepted_findings,
                    "error": f"{type(exc).__name__}:{exc}",
                    "timeout": False,
                    "duration_s": branch_timeout,
                }

        async def _run_ct_branch() -> None:
            """CT log discovery branch with remaining-time-aware asyncio.timeout."""
            self._result.ct_scheduled = True
            if self._ct_log_client is None or duckdb_store is None:
                self._result.ct_terminal_stage = "skipped"
                return
            branch_timeout = self._branch_timeout_s("CT", remaining_s)
            if branch_timeout <= 0:
                log.debug("[F212-B] CT branch skipped: remaining=%.1fs", remaining_s)
                self._result.ct_branch_timed_out = True
                self._result.branch_timeout_count += 1
                self._result.branch_skipped_remaining_too_low += 1
                self._result.ct_log_error = "terminal:remaining_too_low"
                return
            if self._ct_consecutive_timeouts >= 3:
                log.debug("[F26X-E] CT branch skipped: %d consecutive timeouts", self._ct_consecutive_timeouts)
                self._result.ct_log_error = "terminal:circuit_breaker"
                self._result.ct_terminal_stage = "circuit_breaker"
                return
            try:
                self._result.ct_request_attempted = True
                async with asyncio.timeout(branch_timeout):
                    await self._run_ct_log_discovery_in_cycle(query=query, store=duckdb_store)
            except* BaseException as e:
                # PEP 654: asyncio.timeout() raises BaseExceptionGroup containing TimeoutError.
                # asyncio.TaskGroup raises BaseExceptionGroup containing CancelledError.
                # Using except* BaseException handles both — dispatch by actual type.
                if isinstance(e, asyncio.CancelledError):
                    log.debug("[aggressive] CT branch cancelled")
                    raise
                if isinstance(e, TimeoutError):
                    log.debug("[aggressive] CT branch timed out after %ss: %s", branch_timeout, e)
                    self._result.ct_branch_timed_out = True
                    self._result.branch_timeout_count += 1
                    self._result.ct_request_timeout = True
                    self._result.ct_log_error = "terminal:timeout"
                    self._notify_governor_branch_timeout()
                    self._ct_consecutive_timeouts += 1
                else:
                    log.debug("[aggressive] CT branch error: %s", e)
                    self._result.ct_log_error = f"{type(e).__name__}:{e}"
                    self._result.ct_terminal_stage = "error"
            else:
                self._ct_consecutive_timeouts = 0

        outer_timeout = self._branch_timeout_s("cycle_envelope", remaining_s)
        if outer_timeout > 0:
            try:
                async with asyncio.timeout(outer_timeout):
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(_run_feed_branch(), name="sprint:feed_branch", eager_start=True)
                        tg.create_task(_run_public_branch(), name="sprint:public_branch", eager_start=True)
                        tg.create_task(_run_ct_branch(), name="sprint:ct_branch", eager_start=True)
            except* TimeoutError as e:
                # PEP 654: asyncio.timeout() raises BaseExceptionGroup("timeout", (TimeoutError(),))
                # not TimeoutError directly. Using except* is consistent with the other
                # 3× except* Exception/TimeoutError handlers in this file (lines 8998, 11553, 14750).
                # The outer_timeout handler below correctly populates result flags and _public_outcome.
                log.debug("[aggressive] Branch(es) did not complete within %ss: %s", outer_timeout, e)
                self._result.public_branch_timed_out = True
                self._result.ct_branch_timed_out = True
                self._result.ct_request_timeout = True
                self._result.ct_terminal_stage = "request_timeout"
                self._result.branch_timeout_count += 2
                self._result.public_error = "terminal:envelope_timeout"
                self._result.ct_log_error = "terminal:envelope_timeout"
                self._emit_source_family_event(
                    family="PUBLIC", event="timeout", reason="terminal:envelope_timeout", terminal_state="terminal"
                )
                self._emit_source_family_event(
                    family="CT", event="timeout", reason="terminal:envelope_timeout", terminal_state="terminal"
                )
                self._notify_governor_branch_timeout()
                self._notify_governor_branch_timeout()
                self._public_outcome = {
                    "lane": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": self._result.public_discovered,
                    "built_count": 0,
                    "accepted_count": self._result.public_accepted_findings,
                    "error": "terminal:envelope_timeout",
                    "timeout": True,
                    "duration_s": outer_timeout,
                }
            except* Exception as e:
                # PEP 654: asyncio.timeout() wraps TimeoutError in BaseExceptionGroup.
                # except* extracts the Exception sub-group — the surrounding except
                # BaseExceptionGroup would also catch CancelledError which we want
                # to re-raise.  Using except* here is consistent with the other
                # 3× except* Exception handlers in this file (lines 8998, 11553, 14750).
                if isinstance(e, asyncio.CancelledError):
                    raise
                log.debug("[aggressive] Aggressive cycle cancelled: %s", e)
        try:
            from hledac.universal.core.memory_cycle import gc_cycle_maintain

            gc_cycle_maintain(force=False)
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        try:
            self._maybe_call_pressure_relief()
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        if not self._check_hard_deadline():
            log.debug(
                "[D6] Hard deadline exceeded after aggressive branch envelope timeout -- skipping advisory lanes. outer_timeout=%.1fs remaining_s=%.1fs",
                outer_timeout,
                remaining_s,
            )
            return True
        else:
            log.debug(
                "[F212-B] Aggressive gather skipped: outer_timeout=%.1fs (remaining=%.1fs)", outer_timeout, remaining_s
            )
            self._result.public_branch_timed_out = True
            self._result.ct_branch_timed_out = True
            self._result.branch_timeout_count += 2
            self._result.branch_skipped_remaining_too_low += 2
            self._result.public_error = "terminal:remaining_too_low"
            self._result.ct_log_error = "terminal:remaining_too_low"
            self._public_outcome = {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": True,
                "skip_reason": "terminal:remaining_too_low",
                "raw_count": 0,
                "built_count": 0,
                "accepted_count": 0,
                "error": "terminal:remaining_too_low",
                "timeout": True,
                "duration_s": None,
            }
            self._result.ct_terminal_stage = "terminal_by_timeout"
            self._emit_source_family_event(
                family="PUBLIC", event="timeout", reason="terminal:remaining_too_low", terminal_state="terminal"
            )
            self._emit_source_family_event(
                family="CT", event="timeout", reason="terminal:remaining_too_low", terminal_state="terminal"
            )
            self._public_outcome = {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": True,
                "skip_reason": "terminal:remaining_too_low",
                "raw_count": 0,
                "built_count": 0,
                "accepted_count": 0,
                "error": "terminal:remaining_too_low",
                "timeout": False,
                "duration_s": None,
            }
        lanes_timeout = self._branch_timeout_s("ADVISORY", remaining_s)
        _uma = getattr(self._governor, "_uma_state", "ok") if self._governor else "ok"
        if lanes_timeout > 0:
            try:
                async with asyncio.timeout(lanes_timeout):
                    if self._graph_accumulator is None:
                        self._graph_accumulator = SprintGraphAccumulator()
                    _clearnet_max_adv = 4
                    if self._governor is not None:
                        try:
                            _gov_decision_adv = await self._governor.evaluate()
                            _clearnet_max_adv = _gov_decision_adv.fetch_limit
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
                    _streaming_outcomes: list = []
                    async for _outcome_batch in run_enabled_acquisition_lanes_streaming(
                        snapshot=self._acquisition_plan if self._acquisition_plan is not None else None,
                        query=query,
                        store=duckdb_store,
                        uma_state=_uma,
                        clearnet_max=_clearnet_max_adv,
                        seed_context=None,
                        graph_accumulator=self._graph_accumulator,
                        min_finished=0,
                        on_lane_complete=None,
                    ):
                        _streaming_outcomes = list(_outcome_batch)
                        _total_accepted = sum(
                            (
                                getattr(_o, "accepted_findings", 0) or 0
                                for _o in _streaming_outcomes
                                if getattr(_o, "attempted", False)
                            )
                        )
                        if _total_accepted >= 3:
                            break
                    _outcomes = tuple(_streaming_outcomes) if _streaming_outcomes else None
                    if _outcomes:
                        self._lane_outcomes = _outcomes
                        self._result.acquisition_lane_outcomes = _outcomes
                        self._accumulate_lane_findings(_outcomes, query)
                        await self._ingest_ct_lane_candidates(_outcomes, duckdb_store)
                        self._record_quality_rejections_from_store(duckdb_store)
                        ct_findings: list = []
                        for _oc in _outcomes:
                            if getattr(_oc, "source_family", None) == "ct":
                                _cands = getattr(_oc, "candidate_findings", ()) or ()
                                if _cands:
                                    ct_findings.extend(_cands)
                        if ct_findings:
                            await self._run_ct_to_passivedns_active_pivot(
                                ct_findings=ct_findings, duckdb_store=duckdb_store, remaining_s=remaining_s
                            )
                        for _rec in self._result.quality_rejection_ledger or ():
                            try:
                                self._nonfeed_ledger.add_quality_rejection(
                                    source_family=_rec.source_family or "unknown",
                                    reason=_rec.reason or "unknown",
                                    sample_url=getattr(_rec, "url_sample", "") or "",
                                    sample_value=getattr(_rec, "finding_id", "")[:16],
                                )
                            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                pass
            except TimeoutError:
                log.debug("[aggressive] ADVISORY lanes timed out after %ss", lanes_timeout)
            except asyncio.CancelledError:
                raise
            except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                _log_advisory_dedup(
                    log,
                    f"aggressive_advisory_lane_fail:{type(_exc).__name__}",
                    "[aggressive] ADVISORY lane runner exception: %s: %s",
                    type(_exc).__name__,
                    _exc,
                )
        else:
            log.debug("[F212-B] ADVISORY lanes skipped in aggressive: remaining=%.1fs", remaining_s)
        return True

    async def _run_public_discovery_in_cycle(
        self,
        query: str = "",
        duckdb_store: Any = None,
        hermes_engine: Any | None = None,
        memory_manager: Any | None = None,
        public_bootstrap_enabled: bool = False,
        seed_context: Any | None = None,
    ) -> None:
        """

        Sprint 8XE: Run public discovery pipeline in the current cycle.

        P12: Also runs post-storage ToT hypothesis layer when hermes_engine is available.



        Uses asyncio.TaskGroup for bounded concurrency with the feed pipeline.

        Fail-soft: errors are accumulated but never raise or abort the sprint.



        query: real sprint query context from __main__.py (not a weak source hint).

        duckdb_store: DuckDBShadowStore instance for storing findings.

        hermes_engine: Hermes3Engine instance for P12 post-storage ToT (optional, M1 8GB safe).

        memory_manager: MemoryManager instance for session history (optional).

        # F271D: P1.5-fix 2026-06-07 -- seed_context (param) is the canonical
        # local for the function body. Telemetry at line ~15706+ uses the
        # `_seed_ctx_has_any_items()` helper for the domains|urls check.
        # (Earlier fix added `_seed_ctx = seed_context` alias; superseded
        # by direct param access + helper to avoid extra name binding.)
        """
        try:
            async_run_public, PipelineRunResult = _import_live_public_pipeline()
        except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug(f"[8XE] Public pipeline import failed: {exc}")
            self._result.public_error = f"import:{type(exc).__name__}"
            self._public_outcome = {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": False,
                "skip_reason": None,
                "raw_count": self._result.public_discovered,
                "built_count": 0,
                "accepted_count": self._result.public_accepted_findings,
                "error": f"import:{type(exc).__name__}",
                "timeout": False,
                "duration_s": None,
            }
            self._public_pipeline_result = None
            return
        query_hint = query or "OSINT passive discovery"
        _had_error = False
        public_result: Any = None
        try:
            public_result = await async_run_public(
                query=query_hint,
                store=duckdb_store,
                max_results=5,
                fetch_timeout_s=35.0,
                fetch_concurrency=3,
                hermes_engine=hermes_engine,
                memory_manager=memory_manager,
                enqueue_hypothesis_pivot=self.enqueue_hypothesis_pivot,
                public_bootstrap_enabled=public_bootstrap_enabled,
                seed_context=seed_context,
            )
        except asyncio.CancelledError:
            raise
        except ExceptionGroup as eg:
            _had_error = True
            for e in eg.exceptions:
                if isinstance(e, asyncio.CancelledError):
                    raise e
                log.error(f"Public pipeline task failed: {e}")
            self._result.public_error = f"TaskGroup: {type(eg).__name__}"
            self._public_outcome = {
                "lane": "PUBLIC",
                "attempted": True,
                "skipped": False,
                "skip_reason": None,
                "raw_count": self._result.public_discovered,
                "built_count": 0,
                "accepted_count": self._result.public_accepted_findings,
                "error": f"TaskGroup: {type(eg).__name__}",
                "timeout": False,
                "duration_s": None,
            }
            try:
                _psd = {
                    "candidate_providers": [],
                    "selected_provider": None,
                    "rejected_providers": [],
                    "rejection_reasons": {},
                    "provider_errors": [
                        {
                            "provider": "TaskGroup",
                            "error": _sanitize_debug_text(eg, max_chars=500),
                            "error_type": type(eg).__name__,
                        }
                    ],
                    "missing_dependencies": [],
                    "policy_flags": {},
                    "bootstrap_enabled": False,
                    "bootstrap_disabled_reason": "ExceptionGroup",
                    "exception_group_summary": _sanitize_debug_text(eg, max_chars=500),
                }
                self._result.public_provider_selection_debug = _psd
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
            self._public_pipeline_result = None
        if _had_error:
            return
        self._public_pipeline_result = public_result
        self._result.public_discovered += public_result.discovered
        self._result.public_fetched += public_result.fetched
        self._result.public_matched_patterns += public_result.matched_patterns
        self._result.public_accepted_findings += public_result.accepted_findings
        self._result.public_stored_findings += public_result.stored_findings
        if public_result.error:
            self._result.public_error = public_result.error
        self._finding_count += public_result.accepted_findings
        pbv = getattr(public_result, "public_branch_verdict", None)
        if pbv and isinstance(pbv, dict) and (len(self._public_verdicts) < 10):
            self._public_verdicts.append(pbv)
        if getattr(public_result, "backend_degraded", False):
            self._result.public_backend_degraded = True
        _pub_bootstrap_en = getattr(public_result, "public_bootstrap_enabled", False)
        _pub_bootstrap_ord = getattr(public_result, "public_bootstrap_order", "disabled")
        _keyword_seed_fallback = getattr(public_result, "keyword_seed_fallback_triggered", False)
        _pub_bootstrap_prevented = getattr(public_result, "public_bootstrap_prevented_discovery_timeout", False)
        _pub_bootstrap_fetch_att = getattr(public_result, "public_bootstrap_first_fetch_attempted", False)
        _pub_discovered = getattr(public_result, "public_discovered", 0) or 0
        _pub_fetch_candidate = getattr(public_result, "public_fetch_candidate_count", 0) or 0
        _pub_fetch_attempted = getattr(public_result, "public_fetch_attempted", 0) or 0
        _pub_fetch_success = getattr(public_result, "public_fetch_success", 0) or 0
        _pub_fetch_failed = getattr(public_result, "public_fetch_failed", 0) or 0
        _pub_acceptance_attempted = getattr(public_result, "public_acceptance_attempted", 0) or 0
        _pub_acceptance_accepted = getattr(public_result, "public_acceptance_accepted", 0) or 0
        _pub_acceptance_rejected = getattr(public_result, "public_acceptance_rejected", 0) or 0
        _pub_acceptance_reject_reasons = getattr(public_result, "public_acceptance_reject_reasons", {}) or {}
        _pub_terminal_classified = getattr(public_result, "public_terminal_classified_count", 0) or 0
        _pub_unclassified = getattr(public_result, "public_unclassified_count", 0) or 0
        _pub_terminal_reason_counts = getattr(public_result, "public_terminal_reason_counts", {}) or {}
        _pub_skipped_duplicate = getattr(public_result, "public_skipped_duplicate", 0) or 0
        _pub_skipped_scheme = getattr(public_result, "public_skipped_unsupported_scheme", 0) or 0
        _pub_skipped_mem = getattr(public_result, "public_skipped_memory_gate", 0) or 0
        _pub_skipped_quality = getattr(public_result, "public_skipped_quality_gate", 0) or 0
        _security_rejected = getattr(self._result, "security_rejected_count", 0) or 0
        _pii_redacted = getattr(self._result, "pii_redacted_count", 0) or 0
        _pub_skipped_browser = getattr(public_result, "public_skipped_browser_unavailable", 0) or 0
        _pub_skipped_xml = getattr(public_result, "public_skipped_xml_or_feed", 0) or 0
        _pub_skipped_timeout = getattr(public_result, "public_skipped_timeout", 0) or 0
        _pub_skipped_fetch_err = getattr(public_result, "public_skipped_fetch_error", 0) or 0
        _pub_skipped_url_sample = getattr(public_result, "public_skipped_url_sample", ()) or ()
        _pub_rejected_no_pattern = getattr(public_result, "public_rejected_no_pattern_match", 0) or 0
        _pub_rejected_low_info = getattr(public_result, "public_rejected_low_information", 0) or 0
        _pub_rejected_duplicate = getattr(public_result, "public_rejected_duplicate", 0) or 0
        _pub_rejected_storage = getattr(public_result, "public_rejected_storage_rejected", 0) or 0
        _pub_rejected_url_sample = getattr(public_result, "public_rejected_url_samples", ()) or ()
        _pub_accepted_url_sample = getattr(public_result, "public_accepted_url_sample", ()) or ()
        self._public_outcome = {
            "lane": "PUBLIC",
            "attempted": True,
            "skipped": False,
            "skip_reason": None,
            "raw_count": getattr(public_result, "discovered", 0) or 0,
            "built_count": getattr(public_result, "fetched", 0) or 0,
            "accepted_count": getattr(public_result, "accepted_findings", 0) or 0,
            "error": getattr(public_result, "error", None),
            "timeout": getattr(public_result, "timed_out", False),
            "duration_s": getattr(public_result, "elapsed_s", None),
            "public_bootstrap_enabled": _pub_bootstrap_en,
            "public_bootstrap_order": _pub_bootstrap_ord,
            "keyword_seed_fallback_triggered": _keyword_seed_fallback,
            "public_bootstrap_prevented_discovery_timeout": _pub_bootstrap_prevented,
            "public_bootstrap_first_fetch_attempted": _pub_bootstrap_fetch_att,
            "public_discovered": _pub_discovered,
            "public_fetch_candidate_count": _pub_fetch_candidate,
            "public_fetch_attempted": _pub_fetch_attempted,
            "public_fetch_success": _pub_fetch_success,
            "public_fetch_failed": _pub_fetch_failed,
            "public_acceptance_attempted": _pub_acceptance_attempted,
            "public_acceptance_accepted": _pub_acceptance_accepted,
            "public_acceptance_rejected": _pub_acceptance_rejected,
            "public_acceptance_reject_reasons": _pub_acceptance_reject_reasons,
            "public_terminal_classified_count": _pub_terminal_classified,
            "public_unclassified_count": _pub_unclassified,
            "public_terminal_reason_counts": _pub_terminal_reason_counts,
            "public_skipped_duplicate": _pub_skipped_duplicate,
            "public_skipped_unsupported_scheme": _pub_skipped_scheme,
            "public_skipped_memory_gate": _pub_skipped_mem,
            "public_skipped_quality_gate": _pub_skipped_quality,
            "security_rejected_count": _security_rejected,
            "pii_redacted_count": _pii_redacted,
            "public_skipped_browser_unavailable": _pub_skipped_browser,
            "public_skipped_xml_or_feed": _pub_skipped_xml,
            "public_skipped_timeout": _pub_skipped_timeout,
            "public_skipped_fetch_error": _pub_skipped_fetch_err,
            "public_skipped_url_sample": _pub_skipped_url_sample,
            "public_rejected_no_pattern_match": _pub_rejected_no_pattern,
            "public_rejected_low_information": _pub_rejected_low_info,
            "public_rejected_duplicate": _pub_rejected_duplicate,
            "public_rejected_storage_rejected": _pub_rejected_storage,
            "public_rejected_url_samples": _pub_rejected_url_sample,
            "public_accepted_url_sample": _pub_accepted_url_sample,
        }
        self._result.lane_public_accepted_findings = getattr(public_result, "accepted_findings", 0) or 0
        _psd = {
            "candidate_providers": list(getattr(public_result, "public_provider_selected", []) or []),
            "selected_provider": list(getattr(public_result, "public_provider_selected", []) or [])[0]
            if getattr(public_result, "public_provider_selected", None)
            else None,
            "rejected_providers": [
                p.get("provider", "?") for p in getattr(public_result, "public_provider_skipped", []) or []
            ],
            "rejection_reasons": {
                p.get("provider", "?"): _sanitize_debug_text(p.get("reason", ""))
                for p in getattr(public_result, "public_provider_skipped", []) or []
            },
            "provider_errors": [
                {
                    "provider": _sanitize_debug_text(e.get("provider", "?") if isinstance(e, dict) else str(e)),
                    "error": _sanitize_debug_text(e.get("error", "") if isinstance(e, dict) else str(e)),
                    "error_type": e.get("error_type", "?") if isinstance(e, dict) else "?",
                }
                for e in getattr(public_result, "public_provider_errors", []) or []
            ],
            "missing_dependencies": [],
            "policy_flags": {
                "bootstrap_enabled": _pub_bootstrap_en,
                "bootstrap_order": _pub_bootstrap_ord,
                "bootstrap_prevented_discovery_timeout": _pub_bootstrap_prevented,
                "bootstrap_first_fetch_attempted": _pub_bootstrap_fetch_att,
                "public_bootstrap_enabled_at_result": _pub_bootstrap_en,
            },
            "bootstrap_enabled": _pub_bootstrap_en,
            "bootstrap_disabled_reason": "nonfeed_diagnostic_profile_required"
            if not _pub_bootstrap_en and _pub_bootstrap_ord == "disabled"
            else "",
            "seed_context_available": _seed_ctx_has_any_items(seed_context),
            "bootstrap_eligible": _pub_bootstrap_en
            and _pub_bootstrap_ord != "disabled"
            and _seed_ctx_has_any_items(seed_context),
            "bootstrap_used": getattr(public_result, "public_bootstrap_candidates_count", 0) > 0,
            "bootstrap_candidate_count": getattr(public_result, "public_bootstrap_candidates_count", 0) or 0,
            "discovery_error_type": getattr(public_result, "discovery_error_type", "") or "",
            "discovery_elapsed_s": getattr(public_result, "discovery_elapsed_s", None),
        }
        self._result.public_provider_selection_debug = _psd
        log.debug(
            f"[8XE] Public discovery: discovered={public_result.discovered} matched={public_result.matched_patterns} accepted={public_result.accepted_findings}"
        )
        if public_result.accepted_findings > 0 or public_result.stored_findings > 0:
            log.debug(
                "[F205C] Public accepted findings not in scheduler scope for sidecar dispatch (pipeline-internal storage). accepted=%d stored=%d",
                public_result.accepted_findings,
                public_result.stored_findings,
            )
        if ENV.get_bool("HLEDAC_ENABLE_QUERY_ROUTER"):
            try:
                from coordinators.query_router import QueryRouter

                _qr = QueryRouter(source_mask={"dht", "ddg", "wayback"}, max_results_per_source=5, timeout_s=30.0)
                _qr_results = await _qr.query(query_hint, remaining_budget_s=30.0)
                if _qr_results:
                    _ = await self._gate_then_ingest_and_accumulate(
                        duckdb_store, _qr_results, sprint_id=self.sprint_id or ""
                    )
                    log.debug("[F11E] QueryRouter ingested %d findings", len(_qr_results))
            except Exception as _qr_err:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                log.debug("[F11E] QueryRouter failed (non-fatal): %s", _qr_err)

    async def _dispatch_accepted_findings_sidecars(
        self, source_branch: str, findings: list, store: Any, query: str
    ) -> None:
        """

        F205C/F205F: Route accepted findings from any branch through SidecarOrchestrator.



        SidecarOrchestrator dispatches to FindingSidecarBus + individual sidecar runners.

        All batch construction, empty guards, skipped heavy sidecar tracking,

        CancelledError propagation, and fail-soft handling live in the dispatcher.



        Args:

            source_branch: "feed" | "public" | "ct"

            findings: List of accepted CanonicalFinding objects

            store: DuckDBShadowStore instance

            query: Original sprint query

        """
        if self._sidecar_orchestrator is None:
            return
        await self._sidecar_orchestrator.dispatch_findings(
            source_branch=source_branch, findings=findings, store=store, query=query, sprint_id=self.sprint_id or ""
        )

    async def _run_ct_log_discovery_in_cycle(self, query: str, store: Any) -> None:
        """

        Sprint F193A: Run CT log canonical discovery in the current cycle.



        Extracts domain from query, pivots via CTLogClient, converts results

        to CanonicalFinding and ingests into DuckDB store.



        Fail-soft: errors are accumulated but never raise or abort the sprint.

        """
        if self._ct_log_client is None or store is None:
            self._result.ct_terminal_stage = "provider_unavailable"
            return
        self._result.ct_provider_selected = "crtsh"
        import re

        matches = re.findall("[a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?(?:\\.[a-zA-Z]{2,})+", query)
        if not matches:
            self._result.ct_terminal_stage = "no_domain"
            return
        domain = matches[0].lstrip("www.")
        self._result.ct_terminal_stage = "request_attempted"
        session = None
        try:
            from hledac.universal.transport.session_pool import session_pool

            session = await session_pool.httpx()
            ct_result = await self._ct_log_client.pivot_domain(domain, session)
            findings = self._ct_log_client.to_canonical_findings(ct_result, query)
            self._result.ct_bridge_invoked = True
            self._result.ct_raw_count = getattr(ct_result, "raw_count", len(findings))
            self._result.ct_candidates_built = len(findings)
            if findings:
                self._result.ct_terminal_stage = "candidates_built"
            else:
                self._result.ct_terminal_stage = "no_candidates"
            self._result.ct_log_discovered = len(findings)
            if findings:
                if self._enrichment_services:
                    await safe_gather_fire_and_forget(
                        self._enrichment_services.enrich_ct_findings(findings, self._result),
                        self._enrichment_services.enrich_findings_multimodal(findings, self._result),
                        label="enrichment_ct_multimodal_parallel",
                        logger_instance=log,
                    )
                try:
                    self._timer.phase("graph_accum_start")
                    await asyncio.to_thread(self._accumulate_findings_to_graph, findings, self.sprint_id or "")
                finally:
                    self._timer.phase("graph_accum_end")
                try:
                    self._timer.phase("quantum_path_start")
                    new_ioc_values = [f.finding_id for f in findings if hasattr(f, "finding_id") and f.finding_id] or []
                    quantum_seeds = await self._run_quantum_path_analysis(new_ioc_values)
                    if quantum_seeds:
                        self._result.quantum_path_seeds = quantum_seeds
                        from hledac.universal.knowledge.sprint_seeds_store import sync_save_sprint_seeds

                        sync_save_sprint_seeds(self.sprint_id or "", quantum_seeds)
                except Exception as e:  # noqa: BLE001 — best-effort; export/write failure; non-critical
                    logger.debug(f"[SPRINT] quantum_path analysis failed: {e}")
                finally:
                    self._timer.phase("quantum_path_end")
                self._result.ct_storage_attempted = True
                results = await self._gate_then_ingest_and_accumulate(store, findings)
                stored = sum((1 for r in results if isinstance(r, dict) and r.get("accepted")))
                self._result.ct_log_stored = stored
                self._result.ct_storage_accepted = stored > 0
                if stored > 0:
                    self._result.ct_terminal_stage = "storage_accepted"
                else:
                    self._result.ct_terminal_stage = "storage_rejected"
                    if hasattr(store, "get_quality_rejection_ledger"):
                        _ledger = store.get_quality_rejection_ledger()
                        _ct_reasons = tuple((r.reason for r in _ledger if getattr(r, "source_family", "") == "ct_log"))[
                            -10:
                        ]
                        if _ct_reasons:
                            self._result.ct_storage_rejection_reasons = _ct_reasons
                            log.warning(
                                "ct_storage_rejected: candidates=%d stored=%d top_reason=%s reasons=%s",
                                len(findings),
                                stored,
                                _ct_reasons[0] if _ct_reasons else "unknown",
                                _ct_reasons,
                            )
                self._result.ct_log_accepted_findings = stored
                accepted_findings = [
                    f for f, r in zip(findings, results, strict=False) if isinstance(r, dict) and r.get("accepted")
                ]
                await self._dispatch_accepted_findings_sidecars(
                    source_branch="ct", findings=accepted_findings, store=store, query=query
                )
                if ENV.get_bool("HLEDAC_ENABLE_LAYERS") and accepted_findings:
                    try:
                        security = getattr(self._layer_manager, "security", None)
                    except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                        security = None
                    if security is not None and hasattr(security, "validate_finding"):
                        _sec_accepted: list = []
                        _sec_rejected = 0
                        _sec_pii_redacted = 0
                        for f in accepted_findings:
                            try:
                                _ok, _reason = security.validate_finding(f)
                                if not _ok:
                                    _sec_rejected += 1
                                    logger.debug(
                                        "[security_gate] rejected finding %s: %s", getattr(f, "finding_id", ""), _reason
                                    )
                                    continue
                                if _reason == "pii_redacted":
                                    _sec_pii_redacted += 1
                                _sec_accepted.append(f)
                            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                _sec_accepted.append(f)
                        accepted_findings = _sec_accepted
                        self._result.security_rejected_count = (
                            getattr(self._result, "security_rejected_count", 0) + _sec_rejected
                        )
                        self._result.pii_redacted_count = (
                            getattr(self._result, "pii_redacted_count", 0) + _sec_pii_redacted
                        )
                    if ENV.get_bool("HLEDAC_ENABLE_PRIVACY_LAYER"):
                        try:
                            _privacy = self._privacy_layer or getattr(self._layer_manager, "privacy", None)
                            if _privacy and accepted_findings:
                                accepted_findings, _pii_count = await self._run_privacy_gate_async(
                                    accepted_findings, _privacy
                                )
                                if _pii_count > 0:
                                    self._result.pii_findings_anonymized = (
                                        getattr(self._result, "pii_findings_anonymized", 0) + _pii_count
                                    )
                        except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            logger.debug("privacy_gate call failed: %s", _e)
                if ENV.get_bool("HLEDAC_ENABLE_LAYERS") and accepted_findings:
                    try:
                        from hledac.universal.layers import get_temporal_signal_layer
                        from hledac.universal.layers.temporal_signal_layer import event_from_finding_like
                        from hledac.universal.project_types import ActionType

                        ghost = getattr(self._layer_manager, "ghost", None)
                        temporal = get_temporal_signal_layer()
                        security = getattr(self._layer_manager, "security", None)
                    except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        ghost = None
                        temporal = None
                        security = None
                    for f in accepted_findings:
                        if ghost is not None:
                            try:
                                await ghost.execute_action(
                                    ActionType.SCAN,
                                    {"finding_id": getattr(f, "finding_id", "") or "", "query": query},
                                    store_in_vault=True,
                                )
                            except StagnationError:
                                log.critical("[layers] GhostLayer stagnation -- re-raising")
                                raise
                            except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                log.warning("layers GhostLayer.execute_action failed: %s", _e)
                        if temporal is not None:
                            try:
                                te = event_from_finding_like(f)
                                if te:
                                    temporal.observe(te)
                            except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                log.warning("layers TemporalSignalLayer.observe failed: %s", _e)
                        if security is not None:
                            try:
                                audit = getattr(security, "_mission_audit", None)
                                if audit is not None and hasattr(audit, "log_action"):
                                    audit.log_action(
                                        "finding_accepted",
                                        b"",
                                        {"finding_id": getattr(f, "finding_id", "") or "", "query": query},
                                    )
                            except Exception as _e:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                                log.warning("layers SecurityLayer._mission_audit.log_action failed: %s", _e)
                if accepted_findings:
                    await self._sidecar_orchestrator.run_target_memory_update(accepted_findings, store, query)
        except Exception as exc:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            self._result.ct_log_error = str(exc)[:200]
            self._result.ct_terminal_stage = "error"
            log.warning("CT log discovery failed: {exc}", exc=exc)
        finally:
            if session is not None:
                await session.close()

    async def _run_onion_discovery_sidecar(self) -> None:
        """

        Sprint F251: Dark web .onion discovery via Tor.



        Gate: HLEDAC_ENABLE_TOR=1 AND TorTransport circuit established AND

        memory_pressure < 0.70. Fail-soft throughout -- never crashes sprint.



        Sidecar chain position: AFTER _run_ct_log_discovery_in_cycle() (CT logs

        reveal .onion domains from certificate transparency).



        M1 8GB constraints:

        - Semaphore(3): max 3 concurrent Tor crawls

        - 45s per crawl timeout

        - 120s total sidecar budget

        - 20 seeds max per sprint

        """
        import asyncio as asyncio

        if not ENV.get_bool("HLEDAC_ENABLE_TOR"):
            return
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                snap = await governor.evaluate()
                uma_state = getattr(snap, "uma_state", "ok") or "ok"
                if uma_state in ("critical", "emergency"):
                    log.debug("[F251] Onion sidecar skipped: uma_state=%s", uma_state)
                    return
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        try:
            from hledac.universal.transport.tor_transport import TorTransport

            tor = TorTransport()
            if not tor.available:
                log.debug("[F251] TorTransport unavailable (deps missing)")
                return
            if not await tor.is_circuit_established():
                log.debug("[F251] Tor circuit not established -- skipping onion discovery")
                return
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F251] Tor availability check failed: %s", e)
            return
        try:
            from hledac.universal.intelligence.onion_seed_manager import OnionSeedManager

            seed_mgr = OnionSeedManager()
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F251] OnionSeedManager init failed: %s", e)
            return
        try:
            ahmia_query = " ".join(
                [word for word in query.split() if len(word) > 2 and (not word.startswith("http"))][:5]
            )
            if ahmia_query:
                await seed_mgr.discover_from_ahmia(ahmia_query, session=None)
        except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            pass
        seeds = seed_mgr.get_seeds(limit=20)
        if not seeds:
            log.debug("[F251] No onion seeds available")
            return
        log.debug("[F251] Onion discovery: %d seeds, memory pressure ok, Tor ready", len(seeds))
        findings: list = []
        asyncio.Semaphore(3)

        async def crawl_seed(seed: str) -> list:
            """Crawl single .onion seed, convert to CanonicalFinding list."""
            inner_findings: list = []
            try:
                from hledac.universal.intelligence.dark_web_intelligence import (
                    DarkWebCrawler,
                    darkweb_content_to_canonical,
                )

                async with asyncio.timeout(45.0):
                    crawler = DarkWebCrawler(tor_proxy=None)
                    try:
                        await crawler.initialize()
                        query_for_finding = f"onion_discovery:{seed}"
                        async for content in crawler.crawl_onion(seed, depth=0):
                            try:
                                cf = darkweb_content_to_canonical(content, query=query_for_finding)
                                inner_findings.append(cf)
                            except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                                pass
                    finally:
                        await crawler.close()
            except TimeoutError:
                log.debug("[F251] Seed %s timed out after 45s", seed)
            except Exception as e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                log.debug("[F251] Seed %s crawl failed: %s", seed, e)
            return inner_findings

        try:
            async with asyncio.timeout(120.0):
                tasks = [crawl_seed(seed) for seed in seeds]
                results = await safe_gather_ok(*tasks, label="sprint_scheduler:16327")
                for result in results:
                    if isinstance(result, list):
                        findings.extend(result)
        except TimeoutError:
            log.debug("[F251] Onion sidecar timed out after 120s")
        if not findings:
            log.debug("[F251] No findings from onion discovery")
            return
        try:
            duckdb = getattr(self, "_duckdb_store", None)
            if duckdb is not None:
                _ = await self._gate_then_ingest_and_accumulate(duckdb, findings, sprint_id=self.sprint_id or "")
                log.debug("[F251] Ingested %d onion findings", len(findings))
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            _log_advisory_dedup(
                log, f"onion_ingest_fail:{type(e).__name__}", "[F251] Onion findings ingest failed: %s", e
            )

    async def _run_i2p_discovery_sidecar(self) -> None:
        """

        I2P discovery: crawl .i2p addresses found in sprint IOCs.



        Gate: HLEDAC_ENABLE_I2P=1 AND I2PTransport.is_running().

        Memory pressure < 0.70. Fail-soft throughout -- never crashes sprint.



        Sidecar chain position: AFTER _run_onion_discovery_sidecar() if it

        exists, otherwise after CT log discovery.



        M1 8GB constraints:

        - max 5 .i2p addresses per sprint

        - 45s per fetch timeout

        - 120s total sidecar budget

        """
        import asyncio as asyncio

        if not ENV.get_bool("HLEDAC_ENABLE_I2P"):
            return
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                snap = await governor.evaluate()
                uma_state = getattr(snap, "state", "normal") if snap else "normal"
                if uma_state in ("critical", "emergency"):
                    return
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        try:
            from hledac.universal.transport.i2p_transport import I2PTransport
            from hledac.universal.transport.transport_resolver import get_i2p_transport_singleton

            singleton = get_i2p_transport_singleton()
            if singleton is not None and await singleton.is_running():
                return
            i2p = I2PTransport()
            if not await i2p.is_running():
                log.debug("[F2P] I2PTransport not running (is_running=False)")
                return
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F2P] I2PTransport init/check failed: %s", e)
            return
        i2p_addresses: list[str] = []
        duckdb = getattr(self, "_duckdb_store", None)
        if duckdb is not None:
            try:
                con = getattr(duckdb, "_con", None) or getattr(duckdb, "con", None)
                if con is not None:
                    rows = con.execute("SELECT val FROM ioc_nodes WHERE val LIKE '%.i2p' LIMIT 20").fetchmany(20)
                    for row in rows:
                        val = row[0] if row else ""
                        if val:
                            i2p_addresses.append(val)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        seen = set()
        unique_i2p: list[str] = []
        for addr in i2p_addresses:
            if addr not in seen:
                seen.add(addr)
                unique_i2p.append(addr)
        i2p_addresses = unique_i2p[:5]
        if not i2p_addresses:
            log.debug("[F2P] No .i2p addresses found in sprint IOCs")
            return
        log.debug("[F2P] Starting I2P discovery for %d addresses", len(i2p_addresses))
        from hledac.universal.transport.base import TransportConfig

        findings: list = []

        async def fetch_i2p_address(addr: str) -> list:
            """Fetch single .i2p address and convert to CanonicalFinding list."""
            inner: list = []
            try:
                from hledac.universal.knowledge.duckdb_store import CanonicalFinding
                from hledac.universal.utils.source_types import SourceType
            except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                return inner
            try:
                config = TransportConfig(url=f"http://{addr}")
                result = await i2p.fetch(config)
                if result.error:
                    log.debug("[F2P] Fetch %s failed: %s", addr, result.error)
                    return inner
                content = result.content or ""
                if content:
                    from datetime import datetime

                    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

                    finding = CanonicalFinding(
                        finding_id=f"i2p-{addr}-{int(datetime.now(UTC).timestamp() * 1000)}",
                        query="i2p_discovery",
                        source_type=SourceType.I2P_DISCOVERY,
                        confidence=0.5,
                        ts=datetime.now(UTC).isoformat(),
                        provenance=["i2p_transport"],
                        payload_text=content[:4096] if len(content) > 4096 else content,
                    )
                    if self._evidence_log is not None:
                        try:
                            self._evidence_log.create_event(
                                "observation",
                                finding.model_dump() if hasattr(finding, "model_dump") else vars(finding),
                                source_ids=[finding.source_id] if hasattr(finding, "source_id") else [],
                                confidence=getattr(finding, "confidence", 0.5),
                            )
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
                    inner.append(finding)
                    inner.append(finding)
            except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.debug("[F2P] I2P fetch %s failed: %s", addr, e)
            return inner

        try:
            async with asyncio.timeout(120.0):
                tasks = [fetch_i2p_address(addr) for addr in i2p_addresses]
                results = await safe_gather_ok(*tasks, label="sprint_scheduler:16607")
                for result in results:
                    if isinstance(result, list):
                        findings.extend(result)
        except TimeoutError:
            log.debug("[F2P] I2P sidecar timed out after 120s")
        if not findings:
            log.debug("[F2P] No findings from I2P discovery")
            return
        try:
            if duckdb is not None:
                _ = await self._gate_then_ingest_and_accumulate(duckdb, findings, sprint_id=self.sprint_id or "")
                log.debug("[F2P] Ingested %d I2P findings", len(findings))
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            _log_advisory_dedup(log, f"i2p_ingest_fail:{type(e).__name__}", "[F2P] I2P findings ingest failed: %s", e)

    async def _run_dht_sidecar(self) -> None:
        """

        Sprint F214Q: DHT torrent discovery via BitTorrent DHT network.



        INVARIANT: DHT queries NEVER go over Tor -- clearnet UDP only.

        Gate: HLEDAC_ENABLE_DHT=1, max_results=5, timeout=60s.

        Fail-soft: DHT errors logged, never crash sprint.

        """
        import asyncio as asyncio

        if not ENV.get_bool("HLEDAC_ENABLE_DHT"):
            return
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                snap = await governor.evaluate()
                uma_state = getattr(snap, "state", "normal") if snap else "normal"
                if uma_state in ("critical", "emergency"):
                    return
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        log.debug("[F214Q] Starting DHT discovery sidecar")
        try:
            from hledac.universal.dht.kademlia_node import DHT_REAL_UDP
            from hledac.universal.intelligence.dark_web_intelligence import DHTFinding, dht_content_to_canonical

            if not DHT_REAL_UDP:
                log.debug("[F214Q] DHT simulated mode -- skipping sidecar")
                return
            duckdb = getattr(self, "_duckdb_store", None)
            info_hash_seeds: list[str] = []
            try:
                if duckdb is not None:
                    from hledac.universal.knowledge.duckdb_store import duckdb_pool

                    async with duckdb_pool.connection() as conn:
                        rows = await conn.execute(
                            f"SELECT DISTINCT provenance FROM findings WHERE source_type = '{SourceType.CT_LOG}' AND provenance LIKE '%info_hash%' LIMIT 20"
                        ).fetch()
                    for row in rows:
                        prov = row[0] if row else ""
                        if "info_hash:" in prov:
                            ih = prov.split("info_hash:")[1].split(",")[0].strip()
                            if ih:
                                info_hash_seeds.append(ih)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
            if not info_hash_seeds:
                log.debug("[F214Q] No info_hash seeds from CT findings")
                return
            node = self._dht_node
            if node is None:
                log.debug("[F214Q] DHT node not ready -- using standalone crawl")
                try:
                    from hledac.universal.dht.kademlia_node import crawl_dht_for_keyword

                    results = await crawl_dht_for_keyword(self._query, max_results=5, duration_s=30.0)
                    for r in results:
                        if r.get("info_hash"):
                            findings.append(
                                dht_content_to_canonical(
                                    DHTFinding(
                                        info_hash=r["info_hash"],
                                        name=r.get("name", ""),
                                        files=r.get("files", []),
                                        size_bytes=r.get("size_bytes", 0),
                                        peers=r.get("peers", 0),
                                        source="dht_fallback",
                                    ),
                                    query=self._query[:128],
                                )
                            )
                except Exception as e:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                    log.debug("[F214Q] fallback crawl failed: %s", e)
                return
            findings: list = []
            semaphore = asyncio.Semaphore(2)

            async def dht_lookup(info_hash: str) -> list:
                """Lookup single info_hash via DHT singleton."""
                async with semaphore:
                    try:
                        result = await node.lookup_info_hash_metadata(info_hash, timeout_s=30.0)
                        if result and isinstance(result, dict) and result.get("info_hash"):
                            return [result]
                    except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        pass
                    return []

            tasks = [dht_lookup(ih) for ih in info_hash_seeds[:5]]
            async with asyncio.timeout(60.0):
                results = await safe_gather_ok(*tasks, label="sprint_scheduler:16881")
            for batch in results:
                if isinstance(batch, list):
                    for item in batch:
                        try:
                            dht_finding = DHTFinding(
                                info_hash=item.get("info_hash", ""),
                                name=item.get("name", ""),
                                files=item.get("files", []),
                                size_bytes=item.get("size_bytes", 0),
                                peers=item.get("peers", 0),
                                source="dht",
                            )
                            cf = dht_content_to_canonical(dht_finding, query=self._query[:128])
                            findings.append(cf)
                        except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                            pass
            if not findings:
                log.debug("[F214Q] No DHT findings produced")
                return
            if duckdb is not None:
                _ = await self._gate_then_ingest_and_accumulate(duckdb, findings, sprint_id=self.sprint_id or "")
                log.debug("[F214Q] Ingested %d DHT findings", len(findings))
        except TimeoutError:
            log.debug("[F214Q] DHT sidecar timed out after 60s")
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            _log_advisory_dedup(log, f"dht_sidecar_fail:{type(e).__name__}", "[F214Q] DHT sidecar failed: %s", e)

    async def _run_gopher_sidecar(self, query_context: str = "") -> list:
        """
        Sprint F214R: Gopher/Veronica-2 discovery via Floodgap proxy.
        Gate: HLEDAC_ENABLE_GOPHER=1, max_items=50, timeout=30s.
        Fail-soft: Gopher errors logged, never crash sprint.
        """
        if not ENV.get_bool("HLEDAC_ENABLE_GOPHER"):
            return []
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                snap = await governor.evaluate()
                uma_state = getattr(snap, "state", "normal") if snap else "normal"
                if uma_state in ("critical", "emergency"):
                    log.debug("[F214R] Gopher skipped -- memory pressure")
                    return []
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        log.debug("[F214R] Starting Gopher discovery sidecar")
        findings: list = []
        try:
            from hledac.universal.transport.gopher_transport import get_gopher_transport

            gopher = get_gopher_transport()
            query = query_context
            if not query:
                query = getattr(self._result, "next_seeds_query_suggestions", None) or ""
            if query:
                try:
                    response = await gopher.search(query, timeout_s=30.0)
                    if response.items:
                        for item in response.items[:50]:
                            finding_dict = gopher.item_to_finding(
                                item, query=query, sprint_id=getattr(self._result, "sprint_id", None) or ""
                            )
                            findings.append(finding_dict)
                except Exception as e:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                    log.debug("[F214R] Veronica-2 search failed: %s", e)
            if findings and self._duckdb_store is not None:
                _ = await self._gate_then_ingest_and_accumulate(
                    self._duckdb_store, findings, sprint_id=self.sprint_id or ""
                )
                log.debug("[F214R] Ingested %d Gopher findings", len(findings))
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            _log_advisory_dedup(log, f"gopher_sidecar_fail:{type(e).__name__}", "[F214R] Gopher sidecar failed: %s", e)
        self._result.gopher_findings_ingested = len(findings)
        return findings

    async def _run_ipfs_discovery_sidecar(self, cids: list[str] | None = None, query_context: str = "") -> list:
        """

        F218Z: IPFS CID resolution and content fetch via Tor transport.



        Gate: HLEDAC_ENABLE_IPFS=1

        Transport: Tor required (self._tor_transport), NEVER clearnet

        Bounds: max 20 CIDs, 120s timeout per CID, 10MB max file size

        Fail-soft: returns empty list on any error.



        Args:

            cids: List of IPFS CIDs to fetch. If None, extracts from

                  pivot findings or DHT results in the current sprint.

            query_context: Query string for ipfs_search_as_findings fallback.

        """
        if not ENV.get_bool("HLEDAC_ENABLE_IPFS"):
            return []
        try:
            from hledac.universal.transport.tor_transport import TorTransport

            tor = TorTransport()
            if not tor.available:
                log.debug("[F218Z] TorTransport unavailable (deps missing)")
                return []
            if not await tor.is_circuit_established():
                log.debug("[F218Z] Tor circuit not established -- skipping IPFS discovery")
                return []
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F218Z] Tor availability check failed: %s", e)
            return []
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                snap = await governor.evaluate()
                uma_state = getattr(snap, "state", "normal") if snap else "normal"
                if uma_state in ("critical", "emergency"):
                    return []
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        log.debug("[F218Z] Starting IPFS discovery sidecar")
        try:
            from hledac.universal.network.ipfs_client import ipfs_fetch_as_findings, ipfs_search_as_findings

            findings: list = []
            if cids:
                self._result.ipfs_cids_attempted = len(cids)

                async def _fetch_cid(cid: str) -> list:
                    try:
                        return await ipfs_fetch_as_findings(cid, query_context, timeout=120) or []
                    except Exception as e:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                        log.debug("[F218Z] CID fetch failed for %s: %s", cid, e)
                        return []

                _cid_tasks = [_fetch_cid(cid) for cid in cids[:20]]
                _cid_results = await safe_gather_ok(*_cid_tasks, label="sprint_scheduler:ipfs_cid_fetch")
                for _r in _cid_results:
                    if _r and isinstance(_r, list):
                        findings.extend(_r)
            elif query_context:
                self._result.ipfs_cids_attempted += 1
                try:
                    search_results = await ipfs_search_as_findings(query_context, timeout_per_result=120)
                    if search_results:
                        findings.extend(search_results)
                except Exception as e:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                    log.debug("[F218Z] IPFS search failed: %s", e)
            if findings and self._duckdb_store is not None:
                _ = await self._gate_then_ingest_and_accumulate(
                    self._duckdb_store, findings, sprint_id=self.sprint_id or ""
                )
                log.debug("[F218Z] Ingested %d IPFS findings", len(findings))
            return findings
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            _log_advisory_dedup(log, f"ipfs_sidecar_fail:{type(e).__name__}", "[F218Z] IPFS sidecar failed: %s", e)
            return []

    async def _run_digital_ghost_sidecar(self, file_findings: list) -> list:
        """
        Sprint F3FORENSICS: Digital ghost detection on file findings.
        Gate: HLEDAC_ENABLE_DIGITAL_GHOST=1, max_files=10, max_file_size=50MB.
        Fail-soft: errors logged, never crash sprint.
        """
        findings = []
        try:
            if not ENV.get_bool("HLEDAC_ENABLE_DIGITAL_GHOST"):
                return findings
            if self._governor:
                uma = self._governor.get_uma_snapshot()
                if uma.high_water >= 80.0:
                    log.debug("[F3FORENSICS] Digital ghost skipped -- RAM pressure")
                    return findings
            from forensics.enrichment_service import _extract_file_path_from_payload

            MAX_FILES = 10
            file_paths = []
            for f in file_findings:
                fp = _extract_file_path_from_payload(getattr(f, "payload_text", "") or "")
                if fp:
                    file_paths.append((fp, getattr(f, "confidence", 0.5)))
            file_paths.sort(key=lambda x: x[1], reverse=True)
            file_paths = file_paths[:MAX_FILES]
            if not file_paths:
                return findings
            log.debug("[F3FORENSICS] Starting digital ghost detection on %d files", len(file_paths))

            async def analyze_one(path_and_conf):
                path, _ = path_and_conf
                try:
                    from security.digital_ghost_detector import analyze_file_ghosts
                except ImportError:
                    log.debug("[F3FORENSICS] digital_ghost_detector not available")
                    return None
                try:
                    import asyncio

                    result = await asyncio.to_thread(analyze_file_ghosts, path)
                    return result
                except Exception as e:  # noqa: BLE001 — best-effort; thread operation failure; non-critical
                    log.debug("[F3FORENSICS] Ghost analysis failed for %s: %s", path, e)
                    return None

            results = await safe_gather_ok(*[analyze_one(fp) for fp in file_paths], label="sprint_scheduler:17269")
            from core.findings import CanonicalFinding

            for r in results:
                if r is None or isinstance(r, Exception):
                    continue
                try:
                    if hasattr(r, "ghost_signals") and r.ghost_signals:
                        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

                        finding = CanonicalFinding(
                            source_type=SourceType.DIGITAL_GHOST_DETECTION,
                            ioc_type="file",
                            ioc_value=getattr(r, "file_path", ""),
                            confidence=getattr(r, "overall_confidence", 0.5),
                            payload={
                                "ghost_signals_count": len(getattr(r, "ghost_signals", [])),
                                "ghost_signals": [
                                    gs.__dict__ if hasattr(gs, "__dict__") else str(gs)
                                    for gs in getattr(r, "ghost_signals", [])
                                ],
                                "deletion_indicators": getattr(r, "deletion_indicators", False),
                                "recovered_content_count": len(getattr(r, "recovered_content", [])),
                            },
                            tags=["digital_ghost", "forensics"],
                        )
                        findings.append(finding)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            if findings and self._duckdb_store is not None:
                _ = await self._gate_then_ingest_and_accumulate(
                    self._duckdb_store, findings, sprint_id=self.sprint_id or ""
                )
                log.debug("[F3FORENSICS] Ingested %d digital ghost findings", len(findings))
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            _log_advisory_dedup(
                log, f"dghost_sidecar_fail:{type(e).__name__}", "[F3FORENSICS] Digital ghost sidecar failed: %s", e
            )
        return findings

    async def _run_steganography_sidecar(self, image_findings: list) -> list:
        """
        Sprint F3FORENSICS: Steganography detection on image findings.
        Gate: HLEDAC_ENABLE_STEGANOGRAPHY=1, max_images=10, max_image_size=50MB.
        Only emit findings if overall_suspicious > 0.3.
        Fail-soft: errors logged, never crash sprint.
        """
        findings = []
        try:
            if not ENV.get_bool("HLEDAC_ENABLE_STEGANOGRAPHY"):
                return findings
            if self._governor:
                uma = self._governor.get_uma_snapshot()
                if uma.high_water >= 80.0:
                    log.debug("[F3FORENSICS] Stego skipped -- RAM pressure")
                    return findings
            from pathlib import Path

            MAX_IMAGES = 10
            MAX_IMAGE_SIZE_MB = 50
            IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
            image_paths = []
            for f in image_findings:
                fp = getattr(f, "ioc_value", "") or ""
                ext = Path(fp).suffix.lower() if fp else ""
                if ext in IMAGE_EXTENSIONS:
                    image_paths.append((fp, getattr(f, "confidence", 0.5)))
            image_paths.sort(key=lambda x: x[1], reverse=True)
            image_paths = image_paths[:MAX_IMAGES]
            if not image_paths:
                return findings
            log.debug("[F3FORENSICS] Starting steganalysis on %d images", len(image_paths))

            async def analyze_one(path_and_conf):
                path, _ = path_and_conf
                try:
                    from security.stego_detector import StatisticalStegoDetector, StegoConfig
                except ImportError:
                    log.debug("[F3FORENSICS] stego_detector not available")
                    return None
                try:
                    config = StegoConfig(max_image_size=MAX_IMAGE_SIZE_MB * 1024 * 1024)
                    detector = StatisticalStegoDetector(config)
                    await detector.initialize()
                    result = await detector.analyze_image(path)
                    await detector.cleanup()
                    return result
                except Exception as e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                    log.debug("[F3FORENSICS] Stego analysis failed for %s: %s", path, e)
                    return None

            results = await safe_gather_ok(*[analyze_one(fp) for fp in image_paths], label="sprint_scheduler:17380")
            from core.findings import CanonicalFinding

            for r in results:
                if r is None or isinstance(r, Exception):
                    continue
                try:
                    suspicious = getattr(r, "overall_suspicious", False)
                    confidence = getattr(r, "confidence", 0.0)
                    if suspicious and confidence > 0.3:
                        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

                        finding = CanonicalFinding(
                            source_type=SourceType.STEGANOGRAPHY_DETECTION,
                            ioc_type="file",
                            ioc_value=getattr(r, "file_path", ""),
                            confidence=confidence,
                        )
                        if self._evidence_log is not None:
                            try:
                                self._evidence_log.create_event(
                                    "observation",
                                    finding.model_dump() if hasattr(finding, "model_dump") else vars(finding),
                                    source_ids=[finding.source_id] if hasattr(finding, "source_id") else [],
                                    confidence=getattr(finding, "confidence", 0.5),
                                    payload={
                                        "chi_square_score": getattr(r, "chi_square_score", 0.0),
                                        "entropy_score": getattr(r, "entropy_score", 0.0),
                                        "lsb_suspicious": getattr(r, "lsb_suspicious", False),
                                        "stegdetect_available": getattr(r, "stegdetect_available", False),
                                        "stegdetect_result": getattr(r, "stegdetect_result", None),
                                    },
                                    tags=["steganography", "forensics"],
                                )
                            except Exception:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
                                pass
                        findings.append(finding)
                except Exception:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
                    pass
            if findings and self._duckdb_store is not None:
                _ = await self._gate_then_ingest_and_accumulate(
                    self._duckdb_store, findings, sprint_id=self.sprint_id or ""
                )
                log.debug("[F3FORENSICS] Ingested %d steganography findings", len(findings))
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            _log_advisory_dedup(
                log, f"steg_sidecar_fail:{type(e).__name__}", "[F3FORENSICS] Steganography sidecar failed: %s", e
            )
        return findings

    async def _run_bgp_enrichment_sidecar(self) -> list[CanonicalFinding]:
        """F214Q: BGP enrichment -- AS path analysis for IP/ASN seeds.



        Gate: HLEDAC_ENABLE_BGP=1 + M1 memory guard (skip if critical/emergency).

        Seeds: IP/ASN z aktuálních findings (IOC_TYPES: "ip").

        Max 3 IP/ASN per sprint, 30s timeout.

        Semaphore(1) -- BGP queries jsou heavyweight.

        """
        from hledac.universal.network.bgp_monitor import bgp_enrich_to_canonical

        try:
            from ..utils.ioc_extract import IOC_TYPES
        except (ImportError, ModuleNotFoundError):
            IOC_TYPES = ["ip", "asn", "ipv6", "cidr"]
        _bgp_env = ENV.get_bool("HLEDAC_ENABLE_BGP")
        if not _bgp_env:
            return []
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                uma_state = getattr(governor, "_uma_state", "normal")
                if uma_state in ("critical", "emergency"):
                    log.debug("[F214Q] BGP skipped -- memory pressure")
                    return []
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        seed_ips = []
        for finding in getattr(self, "_result", None) or []:
            if hasattr(finding, "query") and finding.query:
                query_lower = finding.query.lower()
                if any((ioc in query_lower for ioc in IOC_TYPES)):
                    import re

                    ips = re.findall("\\b(?:(?:\\d{1,3}\\.){3}\\d{1,3})\\b", finding.query)
                    seed_ips.extend(ips)
        seed_ips = list(set(seed_ips))[:3]
        findings = []
        sem = asyncio.Semaphore(1)

        async def _query_one(ip_or_asn: str) -> list[CanonicalFinding]:
            try:
                async with sem:
                    return await safe_wait_for(
                        bgp_enrich_to_canonical(ip_or_asn, query_context="sprint_enrichment"),
                        timeout=30.0,
                        label="bgp_enrich",
                    )
            except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                return []

        try:
            results = await safe_gather_ok(*[_query_one(ip) for ip in seed_ips], label="sprint_scheduler:17523")
            for r in results:
                if r and (not isinstance(r, Exception)):
                    findings.extend(r)
        except Exception as e:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            log.debug("[F214Q] BGP query failed: %s", e)
        if findings and self._duckdb_store is not None:
            _ = await self._gate_then_ingest_and_accumulate(
                self._duckdb_store, findings, sprint_id=self.sprint_id or ""
            )
            log.debug("[F214Q] Ingested %d BGP enrichment findings", len(findings))
        return findings[:20]

    async def _run_banner_grab_sidecar(self) -> list[CanonicalFinding]:
        """F214Q: Banner grab -- service fingerprinting via TCP probe.



        Gate: HLEDAC_ENABLE_BANNER_GRAB=1 + memory guard.

        Seeds: IP/domain z findings.

        INVARIANT: Banner grab = aktivní TCP probe = CLEARNET ONLY (ne přes Tor).

        Timeout: 10s per port, max 5 portů.

        """
        from hledac.universal.network.banner_grabber import banner_grab_to_canonical

        _banner_env = ENV.get_bool("HLEDAC_ENABLE_BANNER_GRAB")
        if not _banner_env:
            return []
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                uma_state = getattr(governor, "_uma_state", "normal")
                if uma_state in ("critical", "emergency"):
                    log.debug("[F214Q] Banner grab skipped -- memory pressure")
                    return []
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        seed_ips = []
        for finding in getattr(self, "_result", None) or []:
            if hasattr(finding, "provenance"):
                prov = finding.provenance
                if isinstance(prov, tuple) and prov:
                    first = str(prov[0])
                    import re

                    if re.match("\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b", first):
                        seed_ips.append(first)
        seed_ips = list(set(seed_ips))[:3]
        ports_str = ENV.get_str("HLEDAC_BANNER_GRAB_PORTS", default="22,80,443,8080,8443")
        try:
            ports = [int(p.strip()) for p in ports_str.split(",") if p.strip()]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            ports = [22, 80, 443, 8080, 8443]
        ports = ports[:5]
        findings = []

        async def _grab_one(ip: str) -> list[CanonicalFinding]:
            try:
                return await safe_wait_for(
                    banner_grab_to_canonical(ip, ports=ports, query_context="sprint_enrichment"),
                    timeout=60.0,
                    label="banner_grab",
                )
            except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                return []

        try:
            results = await safe_gather_ok(*[_grab_one(ip) for ip in seed_ips], label="sprint_scheduler:17667")
            for r in results:
                if r and (not isinstance(r, Exception)):
                    findings.extend(r)
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F214Q] Banner grab failed: %s", e)
        if findings and self._duckdb_store is not None:
            _ = await self._gate_then_ingest_and_accumulate(
                self._duckdb_store, findings, sprint_id=self.sprint_id or ""
            )
            log.debug("[F214Q] Ingested %d banner grab findings", len(findings))
        return findings[:50]

    def _accumulate_findings_to_graph(self, findings: list, sprint_id: str = "") -> int:
        """

        F198A: Extract IOCs from accepted findings and upsert to graph_service.



        Delegates to SprintGraphAccumulator. Fail-soft: graph errors

        must NOT prevent sprint continuation.



        Returns:

            Number of findings successfully upserted to graph.

        """
        if self._graph_accumulator is None:
            self._graph_accumulator = SprintGraphAccumulator()
        ioc_graph = getattr(self, "_ioc_graph", None)
        if ioc_graph is not None and (not getattr(ioc_graph, "_lock_acquired", True)):
            logger.warning(
                "[GRAPH] IOC graph is READ-ONLY — IOC accumulation disabled (another sprint is holding the graph lock). Findings are still stored in DuckDB."
            )
            return 0
        return self._graph_accumulator.accumulate_findings(findings, sprint_id=sprint_id or "")

    async def _run_privacy_gate_async(self, findings: list, privacy_layer) -> tuple[list, int]:
        """Pre-storage PII anonymization gate.

        Runs BEFORE async_ingest_findings_batch() for ALL storage paths.
        Returns (anonymized_findings, pii_count).

        Scopes: content, raw_content, payload_text, title, summary.
        Fail-soft: never raises -- findings pass through unmodified on any error.

        INVARIANT: Never raises. Always returns input findings on error.
        """
        if privacy_layer is None:
            return (findings, 0)
        pii_count = 0
        anonymized = []
        _ctx_id = getattr(self, "_privacy_context_id", None)
        for f in findings:
            try:
                if isinstance(f, dict):
                    text_fields = {
                        "content": f.get("content") or "",
                        "raw_content": f.get("raw_content") or "",
                        "payload_text": f.get("payload_text") or "",
                        "title": f.get("title") or "",
                        "summary": f.get("summary") or "",
                    }
                else:
                    text_fields = {
                        "content": getattr(f, "content", None) or "",
                        "raw_content": getattr(f, "raw_content", None) or "",
                        "payload_text": getattr(f, "payload_text", None) or "",
                        "title": getattr(f, "title", None) or "",
                        "summary": getattr(f, "summary", None) or "",
                    }
                has_pii = False
                for field_name, field_value in text_fields.items():
                    if not field_value:
                        continue
                    pii_result = privacy_layer.detect_pii(field_value)
                    field_has_pii = (
                        bool(pii_result) if isinstance(pii_result, bool) else any((v for v in pii_result.values() if v))
                    )
                    if field_has_pii:
                        has_pii = True
                        anon_text = privacy_layer.anonymize_text(field_value)
                        try:
                            if isinstance(f, dict):
                                f[field_name] = anon_text
                            else:
                                setattr(f, field_name, anon_text)
                        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            logger.debug(f"[PII] anonymization setattr failed for field {field_name}: {e}")
                if has_pii:
                    pii_count += 1
                    if _ctx_id:
                        try:
                            if isinstance(f, dict):
                                f["_privacy_context_id"] = _ctx_id
                            else:
                                f._privacy_context_id = _ctx_id
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
                anonymized.append(f)
            except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                logger.debug("privacy_gate finding error: %s", _e)
                anonymized.append(f)
        return (anonymized, pii_count)

    async def _gate_then_ingest(self, store: Any, findings: list, sprint_id: str = "") -> Any:
        """F285: PII gate + canonical write for feed lanes.

        When HLEDAC_ENABLE_PRIVACY_LAYER=1, anonymizes PII in
        content/raw_content/payload_text/title/summary BEFORE the
        findings hit async_ingest_findings_batch.

        Fail-soft: never raises. On any error, findings pass through
        to the canonical write path unmodified.

        Args:
            store: duckdb_store (or any object with
                async_ingest_findings_batch). None -> no-op.
            findings: list of CanonicalFinding (or duckdb-compatible
                dicts). Empty -> no-op.

        Returns:
            Whatever async_ingest_findings_batch returns, or None on
            skip / error.
        """
        if store is None or not findings:
            return None
        try:
            _gated: list = findings
            if ENV.get_bool("HLEDAC_ENABLE_PRIVACY_LAYER"):
                try:
                    _privacy = self._privacy_layer or getattr(self._layer_manager, "privacy", None)
                    if _privacy:
                        _gated, _pii_count = await self._run_privacy_gate_async(findings, _privacy)
                        if _pii_count > 0:
                            self._result.pii_findings_anonymized = (
                                getattr(self._result, "pii_findings_anonymized", 0) + _pii_count
                            )
                except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    logger.debug("privacy_gate call failed: %s", _e)
                    _gated = findings
            return await store.drain_and_get_accepted(_gated)
        except Exception as _e:  # noqa: BLE001 — best-effort; logging failure; non-critical
            logger.debug("gate_then_ingest failed: %s", _e)
            return None

    async def _duckdb_background_writer(self) -> None:
        """F285: Background writer that drains _duckdb_write_queue sequentially.

        Enables overlapping DuckDB writes with the next cycle acquisition.
        Sequential draining preserves WAL ordering guarantees.
        Fail-soft: exceptions are logged but do not propagate.

        Event-driven wakeup: uses asyncio.Event instead of 5s timeout polling.
        Notifies writer immediately when items are enqueued. Falls back to
        30s heartbeat to prevent starvation if notify is ever missed.

        BUG-7 FIX: Drain-first shutdown. On shutdown signal, drain all queued
        items BEFORE exiting. This closes the race where findings arriving
        between shutdown.set() and the next queue.get() were silently dropped.
        """
        _drained: list[tuple[Any, list, str]] = []
        self._writer_wakeup.set()
        while True:
            while True:
                try:
                    item = self._duckdb_write_queue.get_nowait()
                    _drained.append(item)
                except asyncio.QueueEmpty:
                    break
            for _store, _findings, _sprint_id in _drained:
                try:
                    await self._gate_then_ingest_and_accumulate(_store, _findings, _sprint_id)
                except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    logger.debug("[F285] writer ingest failed: %s", _e)
                finally:
                    try:
                        self._duckdb_write_queue.task_done()
                    except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                        pass
            _drained.clear()
            self._writer_wakeup.clear()
            try:
                async with asyncio.timeout(30.0):
                    await self._writer_wakeup.wait()
            except TimeoutError:
                if self._duckdb_writer_shutdown.is_set():
                    break
                continue
            except asyncio.CancelledError:
                raise
        while True:
            try:
                _drained.append(self._duckdb_write_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        for _store, _findings, _sprint_id in _drained:
            try:
                await self._gate_then_ingest_and_accumulate(_store, _findings, _sprint_id)
            except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                logger.debug("[F285] shutdown drain failed: %s", _e)
            finally:
                try:
                    self._duckdb_write_queue.task_done()
                except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                    pass
        _drained.clear()

    def _enqueue_duckdb_write(self, store: Any, findings: list, sprint_id: str) -> bool:
        """F285: Enqueue a DuckDB write batch. Returns True if enqueued, False if queue full."""
        try:
            self._duckdb_write_queue.put_nowait((store, findings, sprint_id))
            self._writer_wakeup.set()
            return True
        except asyncio.QueueFull:
            logger.debug("[F285] write queue full, falling back to synchronous")
            return False

    async def _gate_then_ingest_and_accumulate(self, store: Any, findings: list, sprint_id: str = "") -> Any:
        """F266: PII-gated canonical write + graph accumulation.

        Combines _gate_then_ingest (DuckDB write) with _accumulate_findings_to_graph
        (graph upsert) in a single await chain. Fail-soft: graph errors never
        prevent the DuckDB write from completing.


        This is the canonical call for ALL nonfeed lanes (wayback/pdns/doh)
        and sidecars that need graph wiring.


        P0-5: Evidence log events for every finding state transition:
            - CREATED: when findings list is received
            - CANDIDATE: before DuckDB ingest
            - ACCEPTED: ingest result shows accepted findings
            - REJECTED: ingest result shows rejected findings

        Args:
            store: duckdb_store (or any object with async_ingest_findings_batch).
            findings: list of CanonicalFinding.
            sprint_id: Sprint identifier for graph source field.

        Returns:
            Whatever async_ingest_findings_batch returns.
        """
        if store is None or not findings:
            return None
        if self._evidence_log is not None:
            try:
                self._evidence_log.create_event(
                    "observation",
                    {
                        "phase": "CREATED",
                        "findings_count": len(findings),
                        "sprint_id": sprint_id or "",
                        "source": store.__class__.__name__ if hasattr(store, "__class__") else str(type(store)),
                    },
                    source_ids=[],
                    confidence=1.0,
                )
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        if self._evidence_log is not None:
            try:
                _finding_ids = [
                    getattr(f, "finding_id", None) or getattr(f, "source_id", None) or str(hash(str(f)))
                    for f in findings
                ]
                self._evidence_log.create_event(
                    "observation",
                    {"phase": "CANDIDATE", "findings_count": len(findings), "finding_ids": _finding_ids[:20]},
                    source_ids=_finding_ids[:20],
                    confidence=1.0,
                )
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        if len(findings) > _MAX_CHUNK_SIZE:
            _ingest_result = await self._parallel_ingest(findings, store)
            # F320M-R FIX: _parallel_ingest already accumulated to graph internally
            # (see _process_chunk_parallel → _accumulate_findings_to_graph).
            # Skip duplicate post-TaskGroup graph accumulation below by setting None.
            _skip_graph_accum = True
        else:
            # F320M-R FIX: _gate_then_ingest_and_accumulate does DuckDB + graph atomically.
            _ingest_result = await self._gate_then_ingest_and_accumulate(store, findings, sprint_id=sprint_id)
            _skip_graph_accum = False
        if self._evidence_log is not None and _ingest_result is not None:
            try:
                if isinstance(_ingest_result, list):
                    _accepted = sum((1 for r in _ingest_result if isinstance(r, dict) and r.get("accepted")))
                    _rejected = sum((1 for r in _ingest_result if isinstance(r, dict) and (not r.get("accepted"))))
                    if _accepted > 0:
                        self._evidence_log.create_event(
                            "observation",
                            {"phase": "ACCEPTED", "accepted_count": _accepted, "total": len(_ingest_result)},
                            source_ids=[],
                            confidence=1.0,
                        )
                    if _rejected > 0:
                        self._evidence_log.create_event(
                            "observation",
                            {"phase": "REJECTED", "rejected_count": _rejected, "total": len(_ingest_result)},
                            source_ids=[],
                            confidence=1.0,
                        )
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        if not _skip_graph_accum and findings and (_ingest_result is not None):
            try:
                if isinstance(_ingest_result, list) and len(_ingest_result) == len(findings):
                    _accepted_findings = [
                        f for f, r in zip(findings, _ingest_result) if isinstance(r, dict) and r.get("accepted")
                    ]
                else:
                    _accepted_findings = findings
                if len(findings) > 0:
                    _total = len(findings)
                    _accepted = len(_accepted_findings)
                    _rejected = _total - _accepted
                    logger.debug(
                        "[F320] graph_accum: %d/%d accepted (rejected=%d) for sprint_id=%s",
                        _accepted,
                        _total,
                        _rejected,
                        sprint_id or "",
                    )
                if _accepted_findings:
                    if self._graph_accumulator is None:
                        self._graph_accumulator = SprintGraphAccumulator()
                    self._graph_accumulator.accumulate_findings(_accepted_findings, sprint_id=sprint_id or "")
            except Exception as _e:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
                logger.debug("[F266] graph_accumulate failed: %s", _e)
        try:
            if self._temporal_predictor is not None:
                self._temporal_predictor.observe_findings(findings)
        except Exception as _e:  # noqa: BLE001 — best-effort; logging failure; non-critical
            logger.debug("[P3-2] temporal_predictor observe failed: %s", _e)
        return _ingest_result

    async def _process_chunk_parallel(
        self, chunk: list, sem: asyncio.Semaphore, sprint_id: str
    ) -> tuple[list, list | None]:
        """Process one chunk: DuckDB + graph via bounded gather.

        M1 8GB: semaphore limits to _MAX_CHUNK_CONCURRENCY concurrent chunks.
        NOTE: mx.eval([]) barrier is called ONCE after all chunks complete in _parallel_ingest,
        not per-chunk here. This avoids per-chunk barrier overhead.
        """
        async with sem:
            graph_result = None
            try:
                duck_results = None
                if self._duckdb_store:
                    duck_results = await self._duckdb_store.async_ingest_findings_batch(chunk)
                if self._graph_accumulator and duck_results is not None:
                    try:
                        if isinstance(duck_results, list) and len(duck_results) == len(chunk):
                            _accepted_chunk = [
                                f for f, r in zip(chunk, duck_results) if isinstance(r, dict) and r.get("accepted")
                            ]
                        else:
                            _accepted_chunk = chunk
                        if _accepted_chunk:
                            graph_result = self._accumulate_findings_to_graph(_accepted_chunk, sprint_id=sprint_id)
                    except Exception as _e:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
                        logger.debug("[F266] graph_accumulate post-DuckDB failed: %s", _e)
            except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                logger.debug("[F266] chunk parallel failed: %s", e)
                duck_results = None
            # NOTE: mx.eval([]) moved to _parallel_ingest after TaskGroup — single barrier for all chunks
            return (duck_results or [], graph_result)

    async def _parallel_ingest(self, findings: list, store: Any, max_chunk: int = _MAX_CHUNK_SIZE) -> list:
        """Bounded parallel ingest: chunk → TaskGroup → single mx.eval barrier.

        F320M-R FIX: Sequential for-loop replaced with asyncio.TaskGroup for TRUE parallelism.
        Previously chunks ran sequentially even though a Semaphore existed — the await inside
        the for-loop blocked until each chunk completed before starting the next.

        M1 8GB: max _MAX_CHUNK_CONCURRENCY concurrent chunks, single Metal memory barrier after all.
        Returns canonical results (same as async_ingest_findings_batch).
        """
        if not findings:
            return []
        if len(findings) <= max_chunk:
            return await self._gate_then_ingest_and_accumulate(store, findings)
        sem = asyncio.Semaphore(_MAX_CHUNK_CONCURRENCY)
        chunks = [findings[i : i + max_chunk] for i in range(0, len(findings), max_chunk)]
        sprint_id = self.sprint_id or ""

        # F320M-R: TRUE parallel chunk processing via TaskGroup
        # All chunks start immediately (semaphore-controlled), await is per-task not per-loop
        results: list = []
        try:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(self._process_chunk_parallel(chunk, sem, sprint_id), eager_start=True)
                    for chunk in chunks
                ]
            # TaskGroup done — all chunks processed
            results = [r for t in tasks for r in (t.result() or [])]
        except* Exception as e:
            logger.debug("[F320M-R] TaskGroup chunk exception: %s", e)

        # F320M-R: SINGLE mx.eval([]) barrier after ALL parallel chunks complete
        # This releases Metal memory once for the entire batch, not per-chunk
        try:
            import mlx.core as mx

            mx.eval([])
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        return results

    def _sync_latent_relationships_to_graph(self) -> None:
        """

        Wave 2: Export NetworkX latent relationships and upsert unseen ones to DuckPGQ.



        NetworkX discovers relationships (co-occurrence, shared attributes) that are

        NOT yet in DuckPGQ. These are upserted with confidence=0.3 (low-confidence

        inferred relationships) so the knowledge graph learns across sprints.

        """
        if self._rel_discovery_engine is None:
            return
        try:
            nx_graph = self._rel_discovery_engine.export_graph()
            if nx_graph is None:
                return
            from hledac.universal.knowledge.graph_service import _DEFAULT_GRAPH_SERVICE

            seen_rels = _DEFAULT_GRAPH_SERVICE._seen_rels
            added = 0
            for src, dst, data in nx_graph.edges(data=True):
                key = (src, dst, "latent_related")
                if key not in seen_rels:
                    _DEFAULT_GRAPH_SERVICE.upsert_relation(
                        src, dst, "latent_related", weight=data.get("weight", 1.0), evidence="latent_networkx"
                    )
                    seen_rels.add(key)
                    added += 1
            if added:
                log.debug(f"[RelDiscovery] Synced {added} latent relationships to DuckPGQ")
        except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning(f"[RelDiscovery] Latent sync failed: {_e}")

    async def _run_quantum_path_analysis(self, new_ioc_values: list[str]) -> list[str]:
        """
        Sprint F214Q: Post-sprint quantum-inspired graph walk.
        Find undiscovered connected IOCs via DuckPGQGraph.find_connected().

        M1 RAM budget: bounded to 20 IOCs per sprint, max_hops=2, max 1000 total nodes.

        Sprint P1-3: Routes through GraphService.find_entity_history() which
        layers the hot-edges LMDB cache on top of DuckPGQ recursive CTE, giving
        O(1) hot-path lookups for high-degree nodes and falling back to the CTE
        only on cache miss.
        """
        if not ENV.get_bool("HLEDAC_ENABLE_GRAPH_ANALYSIS"):
            return []
        if not new_ioc_values:
            return []
        try:
            batch_map = _DEFAULT_GRAPH_SERVICE.find_connected_batch(new_ioc_values[:20], max_hops=2)
            seen_connections: dict[str, int] = {}
            for ioc_val, connected in batch_map.items():
                if not isinstance(connected, list):
                    continue
                for node in connected:
                    val = node.get("value") if isinstance(node, dict) else str(node)
                    if val and val != ioc_val:
                        seen_connections[val] = seen_connections.get(val, 0) + 1
                        if len(seen_connections) >= 100:
                            break
                if len(seen_connections) >= 100:
                    break
            ranked = sorted(seen_connections.keys(), key=lambda v: seen_connections[v], reverse=True)
            return ranked[:20]
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            logger.debug(f"[SPRINT] quantum_path find_connected_batch failed: {e}")
            return []

    def _record_quality_rejections_from_store(self, store: Any | None) -> None:
        """

        Sprint F216G: Read quality rejection ledger from duckdb_store and

        compute summary dictionaries.



        Called after run_enabled_acquisition_lanes() completes (both advisory

        and aggressive cycles) so that all lane ingest quality gate rejections

        are captured in SprintSchedulerResult.



        Also called from _maybe_dispatch_nonfeed_probe_lanes after its

        direct async_ingest_findings_batch call.



        Invariants (strict):

          - No threshold changes

          - No dedup behavior changes

          - No destructive DB schema migration

          - No benchmark-owned scoring change

        """
        if store is None:
            return
        try:
            if not hasattr(store, "get_quality_rejection_ledger"):
                return
            ledger = store.get_quality_rejection_ledger()
            if not ledger:
                return
            self._result.quality_rejection_ledger = ledger
            quality_sum: dict[str, dict[str, int]] = {}
            dup_sum: dict[str, dict[str, int]] = {}
            low_info_sum: dict[str, dict[str, int]] = {}
            for rec in ledger:
                fam = rec.source_family or "unknown"
                reason = rec.reason or "unknown"
                if reason in ("low_entropy_rejected",):
                    d = low_info_sum.setdefault(fam, {})
                    d[reason] = d.get(reason, 0) + 1
                elif reason in ("persistent_duplicate", "duplicate_detected", "semantic_duplicate"):
                    d = dup_sum.setdefault(fam, {})
                    d[reason] = d.get(reason, 0) + 1
                else:
                    d = quality_sum.setdefault(fam, {})
                    d[reason] = d.get(reason, 0) + 1
            self._result.quality_rejection_summary_by_family = quality_sum
            self._result.duplicate_rejection_summary_by_family = dup_sum
            self._result.low_information_by_family = low_info_sum
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass

    async def _run_ct_to_passivedns_active_pivot(
        self, ct_findings: list, duckdb_store: Any | None, remaining_s: float
    ) -> None:
        """

        Sprint R8: CT -> PassiveDNS active-cycle bounded pivot.



        One-hop pivot from CT lane accepted findings to PassiveDNS lookup.

        Runs after CT lane candidates are ingested in ACTIVE cycle.



        Bounds: default 3 max 5 pivots, depth=1, no recursive, no CT->CT, no PDNS->CT.

        Skips if UMA critical/emergency, remaining time too low, or already ran.



        Flow:

          1. Guard: UMA, time, already-done flag

          2. Select deduplicated domains (max 5)

          3. Run PassiveDNS per domain (monkeypatched in tests)

          4. Convert results via passive_dns_results_to_findings

          5. Ingest via store.async_ingest_findings_batch

          6. Record FAMILY_PIVOT in NonfeedCandidateLedger

          7. Record source_family_outcomes pivot_source=ct, pivot_phase=active



        GHOST_INVARIANTS:

          - gather(return_exceptions=True), CancelledError re-raised

          - Fail-soft: adapter/storage errors never crash sprint

          - No asyncio.run() in async context

        """
        if getattr(self, "_ct_pdns_active_done", False):
            log.debug("[R8] CT->PDNS active pivot skipped: already ran this cycle")
            return
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                snap = await governor.evaluate()
                uma_state = getattr(snap, "uma_state", "ok") or "ok"
                if uma_state in ("critical", "emergency"):
                    log.debug(f"[R8] CT->PDNS active pivot skipped: uma_state={uma_state}")
                    return
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        min_remaining = self._min_branch_remaining_s(remaining_s)
        if remaining_s <= min_remaining:
            log.debug(f"[R8] CT->PDNS active pivot skipped: remaining={remaining_s:.1f}s <= floor={min_remaining:.1f}s")
            return
        if not ct_findings:
            log.debug("[R8] CT->PDNS active pivot: no CT findings to pivot")
            return
        from hledac.universal.runtime.acquisition_strategy import select_ct_domains_for_passivedns_pivot

        pivot_domains = select_ct_domains_for_passivedns_pivot(ct_findings, max_pivots=5)
        if not pivot_domains:
            log.debug("[R8] CT->PDNS active pivot: no domains extracted from CT findings")
            return
        log.debug(f"[R8] CT->PDNS active pivot: {len(pivot_domains)} domains: {pivot_domains[:3]}...")
        self._ct_pdns_active_done = True
        store = getattr(self, "_duckdb_store", None) or duckdb_store
        pdns_results: list = []
        errors: list = []
        from hledac.universal.runtime.source_finding_bridge import passive_dns_results_to_findings
        from hledac.universal.security.passive_dns import PassiveDNSOutcome
        from hledac.universal.security.passive_dns import call_lookup_passive_dns as _pdns_lookup

        async def _run_pdns_for_domain(domain: str) -> tuple[str, list[str], PassiveDNSOutcome | None]:
            try:
                ips, outcome = await _pdns_lookup(domain)
                return (domain, ips, outcome)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                return (domain, [], None)

        try:
            gather_results = await safe_gather_ok(
                *[_run_pdns_for_domain(d) for d in pivot_domains], label="sprint_scheduler:18182"
            )
            for result in gather_results:
                if isinstance(result, BaseException):
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    errors.append(str(result))
                    continue
                domain, ips, outcome = result
                if outcome is not None:
                    pdns_results.append({"domain": domain, "ips": ips, "outcome": outcome})
                else:
                    errors.append(f"domain_{domain}_none")
        except asyncio.CancelledError:
            raise
        except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning("ct_pdns_pivot inner gather failed: %s", _exc)
        if pdns_results and store is not None:
            for res in pdns_results:
                outcome = res["outcome"]
                ips = res["ips"] or []
                domain = res["domain"]
                if not ips or outcome is None:
                    continue
                pdns_findings, pdns_rejections, pdns_telemetry = passive_dns_results_to_findings(
                    ips, outcome, domain, sprint_id=self.sprint_id or ""
                )
                if pdns_findings:
                    try:
                        ingest_results = await self._gate_then_ingest_and_accumulate(
                            store, pdns_findings, sprint_id=self.sprint_id or ""
                        )
                        _stored = sum((1 for r in ingest_results if isinstance(r, dict) and r.get("accepted")))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        log.debug(f"[R8] PDNS ingest failed for {domain}: {exc}")
        ledger = getattr(self, "_nonfeed_ledger", None)
        if ledger is not None:
            for res in pdns_results:
                try:
                    domain = res.get("domain", "")
                    outcome = res.get("outcome", None)
                    if outcome and hasattr(outcome, "result_count"):
                        count = getattr(outcome, "result_count", 0) or 0
                        if count > 0:
                            ledger.add_pivot_discovered(
                                pivot_type="ct_to_passivedns",
                                ioc_value=domain,
                                source_hint=f"ct_domain:{domain}",
                                reason=f"pdns_results={count}",
                            )
                            ledger.add_pivot_stored(pivot_type="ct_to_passivedns", ioc_value=domain, stored_count=count)
                except asyncio.CancelledError:
                    raise
                except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    log.warning("ct_pdns_pivot store loop failed: %s", _exc)
        if pdns_results:
            _sfos = list(getattr(self._result, "source_family_outcomes_list", []) or [])
            for res in pdns_results:
                outcome = res.get("outcome", None)
                if outcome is None:
                    continue
                _sfos.append(
                    {
                        "family": "passive_dns",
                        "lane": "PASSIVE_DNS",
                        "attempted": getattr(outcome, "attempted", True),
                        "accepted": getattr(outcome, "result_count", 0) or 0,
                        "terminal_state": "pivot_ct_domain",
                        "raw_count": 0,
                        "accepted_count": getattr(outcome, "result_count", 0) or 0,
                        "error": getattr(outcome, "error", None),
                        "timeout": getattr(outcome, "timeout", False),
                        "skipped": False,
                        "pivot_source": "ct",
                        "pivot_phase": "active",
                        "pivot_domains": pivot_domains,
                    }
                )
            self._result.source_family_outcomes_list = _sfos
        log.debug(f"[R8] CT->PDNS active pivot done: {len(pdns_results)} domains with results, errors={len(errors)}")

    def _accumulate_lane_findings(self, outcomes: tuple, query: str) -> None:
        """

        Sprint F207J-A: Accumulate accepted lane findings into scheduler truth.

        [F207K-A] Extended with bridge rejection tracking.



        Populates:

          - _result.lane_*_accepted_findings counters

          - _lane_verdicts accumulator (for feed_verdict analog per lane)

          - _all_findings (bounded at 500, same cap as feed findings)

          - _lane_rejections (source_family, rejection_reason, rejected_count, samples)



        Also updates source_family_outcomes in the diagnostic report.



        Args:

            outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes.

            query: Sprint query string (used for _all_findings entry).

        """
        _LANE_SOURCE_MAP = {
            AcquisitionLane.CT: "ct",
            AcquisitionLane.WAYBACK: "wayback_archive",
            AcquisitionLane.PASSIVE_DNS: "passive_dns",
            AcquisitionLane.BLOCKCHAIN: "blockchain",
            AcquisitionLane.IPFS: "ipfs",
            AcquisitionLane.PUBLIC: "public",
        }
        for outcome in outcomes:
            if not getattr(outcome, "attempted", False):
                continue
            lane_name = getattr(outcome, "lane", None)
            accepted = getattr(outcome, "accepted_findings", 0) or 0
            produced = getattr(outcome, "produced_items", 0) or 0
            error = getattr(outcome, "error", None)
            duration = getattr(outcome, "duration_s", 0.0) or 0.0
            source_family = getattr(outcome, "source_family", None) or "unknown"
            rejected_count = getattr(outcome, "rejected_count", 0) or 0
            rejection_reasons = getattr(outcome, "rejection_reasons", ()) or ()
            sample_rejections = getattr(outcome, "sample_rejections", ()) or ()
            match lane_name:
                case AcquisitionLane.CT:
                    self._result.lane_ct_accepted_findings += accepted
                case AcquisitionLane.WAYBACK:
                    self._result.lane_wayback_accepted_findings += accepted
                case AcquisitionLane.PASSIVE_DNS:
                    self._result.lane_pdns_accepted_findings += accepted
                case AcquisitionLane.BLOCKCHAIN:
                    self._result.lane_blockchain_accepted_findings += accepted
                case AcquisitionLane.IPFS:
                    self._result.lane_ipfs_accepted_findings += accepted
                case AcquisitionLane.DOH:
                    self._result.lane_doh_accepted_findings += accepted
                case _:
                    pass
            wayback_raw = getattr(outcome, "wayback_raw_count", 0) or 0
            passive_dns_raw = getattr(outcome, "passive_dns_raw_count", 0) or 0
            candidate_findings = getattr(outcome, "candidate_findings", ()) or ()
            if lane_name == AcquisitionLane.WAYBACK:
                self._result.wayback_attempted = True
                self._result.wayback_raw_count += wayback_raw
                self._result.wayback_candidates_built += len(candidate_findings)
                self._result.wayback_accepted_count += accepted
            elif lane_name == AcquisitionLane.PASSIVE_DNS:
                self._result.passive_dns_attempted = True
                self._result.passive_dns_raw_count += passive_dns_raw
                self._result.passive_dns_candidates_built += len(candidate_findings)
                self._result.passive_dns_accepted_count += accepted
            if accepted > 0:
                verdict_tag = _LANE_SOURCE_MAP.get(lane_name, "unknown_lane")
                quality = 1 if not error else 0
                self._lane_verdicts.extend([(verdict_tag, accepted, 0, 0, quality)])
                candidate_findings = getattr(outcome, "candidate_findings", ()) or ()
                remaining = self._MAX_FINDINGS_PER_SPRINT - len(self._all_findings)
                if candidate_findings and remaining > 0:
                    _cf_slice = candidate_findings[:remaining]
                    _new_entries = []
                    _errored = 0
                    for cf in _cf_slice:
                        try:
                            _new_entries.append(
                                {
                                    "type": f"lane_{getattr(cf, 'source_type', verdict_tag) or verdict_tag}",
                                    "source": getattr(cf, "source_type", verdict_tag) or verdict_tag,
                                    "matched_patterns": produced,
                                    "accepted_findings": accepted,
                                    "severity": "medium",
                                    "confidence": getattr(cf, "confidence", 0.5) or 0.5,
                                    "description": str(
                                        getattr(cf, "payload_text", "bridge finding") or "bridge finding"
                                    )[:200],
                                    "ts": getattr(cf, "ts", 0.0) or 0.0,
                                }
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            _errored += 1
                            continue
                    if _new_entries:
                        self._all_findings.extend(_new_entries)
                _already_graphed_in_lane = {
                    AcquisitionLane.CT,
                    AcquisitionLane.WAYBACK,
                    AcquisitionLane.PASSIVE_DNS,
                    AcquisitionLane.DOH,
                    AcquisitionLane.BLOCKCHAIN,
                    AcquisitionLane.IPFS,
                    AcquisitionLane.ACADEMIC,
                }
                if candidate_findings and lane_name not in _already_graphed_in_lane:
                    try:
                        self._accumulate_findings_to_graph(candidate_findings, sprint_id=self.sprint_id or "")
                    except Exception:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
                        log.debug("[F265C] Lane graph accumulation failed (non-fatal)")
                elif remaining > 0:
                    if len(self._all_findings) < self._MAX_FINDINGS_PER_SPRINT:
                        self._all_findings.append(
                            {
                                "type": f"lane_{verdict_tag}",
                                "source": verdict_tag,
                                "matched_patterns": produced,
                                "accepted_findings": accepted,
                                "severity": "medium",
                                "confidence": quality * 0.8,
                                "description": f"{accepted} accepted findings from {verdict_tag} lane in {duration:.1f}s",
                            }
                        )
            if rejected_count > 0 and source_family != "unknown":
                if not hasattr(self, "_lane_rejections") or self._lane_rejections is None:
                    self._lane_rejections = []
                    self._lane_rejections_total_seen = 0
                    self._lane_rejections_dropped = 0
                verdict_tag = _LANE_SOURCE_MAP.get(lane_name, "unknown_lane")
                reason_counts: dict[str, int] = {}
                for reason in rejection_reasons:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                for reason, count in reason_counts.items():
                    self._lane_rejections_total_seen += count
                    self._lane_rejections.append(
                        {
                            "source_family": source_family,
                            "rejection_reason": reason,
                            "rejected_count": count,
                            "sample": list(sample_rejections[:3]),
                            "verdict_tag": verdict_tag,
                            "lane_name": str(lane_name) if lane_name else "unknown",
                        }
                    )
                    if len(self._lane_rejections) > MAX_LANE_REJECTIONS:
                        self._lane_rejections_dropped += len(self._lane_rejections) - MAX_LANE_REJECTIONS
                        self._lane_rejections = self._lane_rejections[-MAX_LANE_REJECTIONS:]

    def _ingest_feed_public_candidates_to_ledger(self) -> None:
        """

        F214: Bridge feed and PUBLIC findings into nonfeed candidate ledger.



        Extracts domain candidates from feed/public lane outcomes and records them

        in the ledger for downstream nonfeed lane planning (DOH, CT, Wayback, passiveDNS).



        Flow:

          1. Extract domain candidates from _lane_outcomes (FEED/PUBLIC lanes)

          2. Apply source_host filtering (deprioritize domains that appear only

             in source URL hostname, not in content body)

          3. Rank candidates by confidence and seen_count

          4. Record via add_feed_candidate() for FEED family

          5. Compute lane eligibility from candidates



        Bounding:

          - MAX_DOMAIN_CANDIDATES_FOR_LANES (10) max candidates processed

          - MAX_FEED_CANDIDATES (10) per source URL

          - fail-soft throughout -- ledger errors never crash sprint



        Lane eligibility telemetry:

          - Stored in result.nonfeed_lane_eligibility after computation

        """
        try:
            from hledac.universal.runtime.nonfeed_candidate_ledger import (
                FAMILY_FEED,
                FAMILY_PUBLIC,
                MAX_DOMAIN_CANDIDATES_FOR_LANES,
                MAX_FEED_CANDIDATES,
                compute_lane_eligibility,
                extract_domain_candidates_from_text,
                filter_source_host_only,
                rank_candidates,
            )
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return
        try:
            outcomes = getattr(self, "_lane_outcomes", None) or ()
            if not outcomes:
                return
            all_candidates: list = []
            source_url_by_family: dict[str, str] = {}
            for outcome in outcomes:
                if not getattr(outcome, "attempted", False):
                    continue
                family = getattr(outcome, "source_family", None) or ""
                source_family = FAMILY_FEED if family == "feed" else FAMILY_PUBLIC
                candidates = getattr(outcome, "candidate_findings", ()) or ()
                if not candidates:
                    continue
                source_url = ""
                for c in candidates:
                    prov = getattr(c, "provenance", None) or ()
                    if isinstance(prov, (list, tuple)) and prov:
                        source_url = str(prov[0]) if prov else ""
                        break
                if source_url:
                    source_url_by_family[source_family] = source_url
                for c in candidates:
                    payload_text = getattr(c, "payload_text", "") or ""
                    query = getattr(c, "query", "") or ""
                    if payload_text:
                        text_candidates = extract_domain_candidates_from_text(
                            payload_text, source_url=source_url if source_url else None, source_family=source_family
                        )
                        for tc in text_candidates[:MAX_FEED_CANDIDATES]:
                            all_candidates.append(tc)
                    if query:
                        query_candidates = extract_domain_candidates_from_text(
                            query, source_url=source_url if source_url else None, source_family=source_family
                        )
                        for qc in query_candidates[:MAX_FEED_CANDIDATES]:
                            if not any((tc.domain == qc.domain for tc in all_candidates)):
                                all_candidates.append(qc)
            if not all_candidates:
                return
            seen: dict[str, object] = {}
            deduped: list = []
            for tc in all_candidates:
                key = f"{tc.domain}|{tc.source_field}"
                if key not in seen:
                    seen[key] = tc
                    deduped.append(tc)
                else:
                    existing = seen[key]
                    from dataclasses import replace

                    deduped.append(replace(existing, seen_count=existing.seen_count + 1))
            all_candidates = deduped
            source_host_domains = frozenset()
            if source_url_by_family:
                for _sf, surl in source_url_by_family.items():
                    if surl:
                        filtered, sh_domains = filter_source_host_only(all_candidates, surl)
                        all_candidates = filtered
                        source_host_domains = sh_domains
                        break
            ranked = rank_candidates(
                all_candidates, max_total=MAX_DOMAIN_CANDIDATES_FOR_LANES, source_host_domains=source_host_domains
            )
            mv = get_domain_mv()
            for tc in ranked:
                try:
                    self._nonfeed_ledger.add_feed_candidate(
                        domain=tc.domain,
                        source_field=tc.source_field,
                        confidence=tc.confidence,
                        reason=f"{tc.reason} (seen={tc.seen_count})",
                        sample_context=tc.sample_context[:200] if tc.sample_context else "",
                    )
                    try:
                        mv.upsert_candidate(
                            domain=tc.domain,
                            source_family=getattr(tc, "source_field", "") or "feed",
                            ioc_type="domain",
                            confidence=tc.confidence,
                            rank_score=getattr(tc, "rank_score", None),
                            nonfeed_eligible_ct=eligibility.get("ct", {}).get("eligible", False)
                            if isinstance(eligibility, dict)
                            else False,
                            nonfeed_eligible_doh=eligibility.get("doh", {}).get("eligible", False)
                            if isinstance(eligibility, dict)
                            else False,
                            nonfeed_eligible_wayback=eligibility.get("wayback", {}).get("eligible", False)
                            if isinstance(eligibility, dict)
                            else False,
                            nonfeed_eligible_pdns=eligibility.get("passive_dns", {}).get("eligible", False)
                            if isinstance(eligibility, dict)
                            else False,
                        )
                    except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        pass
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            eligibility = compute_lane_eligibility(ranked)
            self._result.nonfeed_lane_eligibility = eligibility
            if ranked:
                doh_domains = [tc.domain for tc in ranked if not tc.domain[0].isdigit()][:5]
                ct_domains = [tc.domain for tc in ranked if not tc.domain[0].isdigit()][:10]
                wayback_candidates = [tc.domain for tc in ranked][:10]
                pdns_candidates = [tc.domain for tc in ranked if not tc.domain[0].isdigit() or tc.domain[0].isdigit()][
                    :10
                ]
                self._result.nonfeed_doh_planner_input = doh_domains
                self._result.nonfeed_ct_planner_candidates = ct_domains
                self._result.nonfeed_wayback_candidates = wayback_candidates
                self._result.nonfeed_passive_dns_candidates = pdns_candidates
                domain_tuples = tuple((tc.domain for tc in ranked if not tc.domain[0].isdigit()))
                if domain_tuples:
                    self._result.feed_domain_seeds = domain_tuples[:10]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass

    async def _ingest_ct_lane_candidates(self, outcomes: tuple, duckdb_store: Any) -> None:
        """

        Sprint R1B: Ingest CT lane CanonicalFinding candidates via DuckDBShadowStore.



        Bridges the gap between the acquisition lane's ct_results_to_findings() output

        (which produces CanonicalFinding dicts in candidate_findings) and the canonical

        storage path (async_ingest_findings_batch).



        Flow per CT outcome with candidates:

          1. Extract candidate_findings from CT AcquisitionLaneOutcome

          2. Call duckdb_store.async_ingest_findings_batch(candidates)

          3. Record storage results in NonfeedCandidateLedger (stored / quarantine / provider_failed)

          4. Update _result.lane_ct_accepted_findings with accepted count



        Fail-soft: storage errors never crash the sprint.

        CancelledError: re-raised to caller (GHOST_INVARIANTS I6).

        M1/UMA: no MLX model load in this path.



        Args:

            outcomes:   Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes.

            duckdb_store: DuckDBShadowStore instance for canonical storage.

        """
        if not duckdb_store:
            return
        for outcome in outcomes:
            if getattr(outcome, "source_family", None) != "ct":
                continue
            if not getattr(outcome, "attempted", False):
                continue
            candidates = getattr(outcome, "candidate_findings", ()) or ()
            if not candidates:
                continue
            rejection_reasons = getattr(outcome, "rejection_reasons", ()) or ()
            getattr(outcome, "sample_rejections", ()) or ()
            for rej in rejection_reasons[:5]:
                try:
                    reason_str = getattr(rej, "reject_reason", str(rej)) or "bridge_rejection"
                    domain_str = getattr(rej, "domain", "") or ""
                    self._nonfeed_ledger.add_ct_quarantine(
                        domain=domain_str,
                        reject_reason=reason_str,
                        source_url="",
                        query=getattr(outcome, "ct_query", "") or "",
                    )
                except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                    pass
            try:
                storage_results = await self._gate_then_ingest_and_accumulate(
                    duckdb_store, list(candidates), sprint_id=self.sprint_id or ""
                )
                accepted_count = 0
                for i, result in enumerate(storage_results):
                    is_accepted = (
                        getattr(result, "lmdb_success", False)
                        if hasattr(result, "lmdb_success")
                        else result.get("lmdb_success", False)
                        if isinstance(result, dict)
                        else False
                    )
                    candidate = candidates[i] if i < len(candidates) else None
                    candidate_id = getattr(candidate, "finding_id", "")[:32] if candidate else ""
                    domain = getattr(candidate, "query", "") if candidate else ""
                    if is_accepted:
                        accepted_count += 1
                        try:
                            self._nonfeed_ledger.add_public_event(
                                stage="stored",
                                candidate_id=candidate_id,
                                reason="ct_stored",
                                accepted=True,
                                sample_url="",
                                sample_value=domain[:64] if domain else candidate_id[:16],
                            )
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
                    else:
                        try:
                            self._nonfeed_ledger.add_public_event(
                                stage="quarantine",
                                candidate_id=candidate_id,
                                reason="ct_quarantine",
                                accepted=False,
                                sample_url="",
                                sample_value=domain[:64] if domain else candidate_id[:16],
                            )
                        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                            pass
                if accepted_count > 0:
                    self._result.lane_ct_accepted_findings = (
                        getattr(self._result, "lane_ct_accepted_findings", 0) + accepted_count
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass

    async def _run_target_memory_update(self, findings: list[Any], store: Any, query: str) -> None:
        """

        F204D: Update cross-sprint target memory after findings are accepted.



        Sidecar runs after findings are accepted and sidecar bus completes.

        Extracts entity/exposure/pivot facets from findings and merges into

        target memory via duckdb_store.



        RAM guard: skip if RSS > high_water (85% threshold).

        Fail-soft: errors never crash the sprint.



        Args:

            findings: List of CanonicalFinding that were accepted and stored

            store: DuckDBShadowStore instance for async_upsert_target_memory

            query: Original sprint query (used as target context)

        """
        if not findings or store is None:
            return
        try:
            import psutil
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return
        try:
            process = psutil.Process()
            mem_info = process.memory_info()
            rss_mb = mem_info.rss / 1024**2
            vm = psutil.virtual_memory()
            high_water = vm.percent * 0.85
            if rss_mb > high_water:
                return
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        entity_facets: dict[str, Any] = {}
        exposure_facets: dict[str, Any] = {}
        pivot_facets: dict[str, Any] = {}
        for finding in findings:
            target_id = getattr(finding, "target_id", None) or getattr(finding, "entity_id", None)
            if not target_id:
                continue
            if hasattr(finding, "entity_type"):
                if target_id not in entity_facets:
                    entity_facets[target_id] = {"types": set(), "count": 0}
                entity_facets[target_id]["types"].add(getattr(finding, "entity_type", "unknown"))
                entity_facets[target_id]["count"] += 1
            if hasattr(finding, "source_type") and getattr(finding, "source_type", None) == "exposure":
                if target_id not in exposure_facets:
                    exposure_facets[target_id] = {"signals": [], "count": 0}
                exposure_facets[target_id]["signals"].append(getattr(finding, "signal_type", "unknown"))
                exposure_facets[target_id]["count"] += 1
            if hasattr(finding, "suggested_pivots"):
                pivots = getattr(finding, "suggested_pivots", [])
                for pivot in pivots[:5]:
                    f"{pivot.get('pivot_type', '')}:{pivot.get('ioc_value', '')}"
                    if target_id not in pivot_facets:
                        pivot_facets[target_id] = {"pivots": [], "count": 0}
                    pivot_facets[target_id]["pivots"].append(pivot)
                    pivot_facets[target_id]["count"] += 1
            if hasattr(finding, "source_type") and getattr(finding, "source_type", None) == "rir_correlation":
                payload_text = getattr(finding, "payload_text", None) or ""
                try:
                    rir_data = (
                        _msgspec_decode(payload_text) if isinstance(payload_text, (str, bytes, bytearray)) else {}
                    )
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    rir_data = {}
                asn = rir_data.get("asn", "") or ""
                org = rir_data.get("org", "") or ""
                netblock = rir_data.get("netblock", "") or ""
                country = rir_data.get("country", "") or ""
                ioc_type = rir_data.get("ioc_type", "") or ""
                ioc_value_from_payload = rir_data.get("ioc_value", "") or getattr(finding, "ioc_value", "") or ""
                if target_id not in exposure_facets:
                    exposure_facets[target_id] = {"signals": [], "rir_asns": {}, "count": 0}
                rir_asns = exposure_facets[target_id].setdefault("rir_asns", {})
                if asn:
                    rir_asns[asn] = {
                        "org": org,
                        "netblock": netblock,
                        "country": country,
                        "ioc_type": ioc_type,
                        "ioc_value": ioc_value_from_payload,
                    }
                exposure_facets[target_id]["count"] += 1
        for tid in entity_facets:
            entity_facets[tid]["types"] = list(entity_facets[tid]["types"])[:MAX_MEMORY_ENTITIES]
        for tid in list(exposure_facets.keys()):
            exposure_facets[tid]["signals"] = exposure_facets[tid]["signals"][:MAX_MEMORY_EXPOSURES]
            if "rir_asns" in exposure_facets[tid]:
                rir_asns = exposure_facets[tid]["rir_asns"]
                if len(rir_asns) > 100:
                    exposure_facets[tid]["rir_asns"] = dict(list(rir_asns.items())[:100])
        for tid in list(pivot_facets.keys()):
            pivot_facets[tid]["pivots"] = pivot_facets[tid]["pivots"][:MAX_MEMORY_PIVOTS]
        now = _time.time()
        for target_id in set(entity_facets.keys()) | set(exposure_facets.keys()) | set(pivot_facets.keys()):
            update = TargetMemoryUpdate(
                target_id=target_id,
                sprint_id=self.sprint_id or "",
                finding_count=len(findings),
                entity_facets=entity_facets.get(target_id, {}),
                exposure_facets=exposure_facets.get(target_id, {}),
                pivot_facets=pivot_facets.get(target_id, {}),
                observed_ts=now,
            )
            if self._target_memory_service is None:
                self._target_memory_service = TargetMemoryService()
            merged = self._target_memory_service.merge_update(update)
            try:
                await store.async_upsert_target_memory(merged)
            except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                pass

    async def _run_advisory_runner(self) -> None:
        """

        F206D: Delegate all advisory orchestration to SidecarOrchestrator.



        SidecarOrchestrator.run_advisory_runner() owns:

          1. run_all_advisories (pivot_planner, pivot_executor, resource_governor, analyst_brief)

          2. run_ct_to_passivedns_pivot_advisory

          3. run_bgp_advisory_sidecar (non-blocking)

          4. run_wayback_cdx_deep_sidecar (non-blocking)



        This method remains for backward compatibility with any direct callers.

        """
        await self._sidecar_orchestrator.run_advisory_runner()

    async def _run_ane_semantic_dedup_advisory(self) -> None:
        """
        Sprint F265B-III: ANE-backed semantic deduplication of findings.

        Runs after all advisory steps have completed, on the full findings list.
        Uses ANE CoreML MiniLM embeddings to detect near-duplicate findings that
        the RotatingBloomFilter URL dedup misses (similar title+snippet, not exact URL).

        Bounded:
          - threshold = 0.92 cosine similarity
          - Only runs when ANE embedder is loaded (fail-soft if unavailable)
          - No changes to canonical write path (DuckDB/LMDB untouched)

        Returns:
            None. Findings list is updated in-place via self._result.all_findings.
        """
        all_findings: list[dict] = getattr(self._result, "all_findings", None)
        if not all_findings or len(all_findings) < 2:
            return
        try:
            from hledac.universal.brain.ane_embedder import semantic_dedup_findings

            threshold = ENV.get_float("HLEDAC_ANE_DEDUP_THRESHOLD", default=0.92)
            original_count = len(all_findings)
            deduped = await asyncio.to_thread(semantic_dedup_findings, all_findings, threshold=threshold)
            if deduped is not None and len(deduped) < original_count:
                self._result.all_findings = deduped
                removed = original_count - len(deduped)
                logger.debug(
                    "[ANE-DEDUP] removed %d near-duplicates, %d → %d findings", removed, original_count, len(deduped)
                )
                if hasattr(self._result, "ane_dedup_removed"):
                    self._result.ane_dedup_removed = removed
            else:
                logger.debug("[ANE-DEDUP] no near-duplicates found")
        except Exception as _e:  # noqa: BLE001 — best-effort; logging failure; non-critical
            logger.debug("[ANE-DEDUP] failed (fail-soft): %s", _e)

    async def _run_ct_to_passivedns_pivot_advisory(self) -> None:
        """

        Sprint R5: CT accepted domains -> PassiveDNS one-hop pivot.



        One-hop pivot from CT lane accepted findings to PassiveDNS lookup.

        No recursive pivoting (pivot depth = 1).

        No new queue framework.

        No stealth/browser.



        Flow:

          1. Extract CT accepted domains from acquisition lane outcomes

          2. Deduplicate (max 10 via dict.fromkeys)

          3. Guard: skip if UMA critical/emergency

          4. For each domain: call PassiveDNS (monkeypatched in tests)

          5. Record FAMILY_PIVOT in NonfeedCandidateLedger

          6. Record source_family_outcomes pivot_source=ct



        GHOST_INVARIANTS:

          - gather(return_exceptions=True)

          - Manual CancelledError filter + error collection after gather

          - CancelledError re-raised

          - No MLX model load

          - No asyncio.run() in async context

          - Bounded: max 10 pivot domains

          - Fail-soft: pivot error never crashes sprint

        """
        try:
            governor = getattr(self, "_governor", None)
            if governor is not None:
                snap = await governor.evaluate()
                uma_state = getattr(snap, "uma_state", "ok") or "ok"
                if uma_state in ("critical", "emergency"):
                    log.debug(f"[R5] CT->PDNS pivot skipped: uma_state={uma_state}")
                    return
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        ct_findings: list = []
        try:
            outcomes = getattr(self._result, "acquisition_lane_outcomes", ()) or ()
            for outcome in outcomes:
                if getattr(outcome, "source_family", None) != "ct":
                    continue
                if not getattr(outcome, "attempted", False):
                    continue
                candidates = getattr(outcome, "candidate_findings", ()) or ()
                accepted = getattr(outcome, "accepted_count", 0) or 0
                if accepted > 0 and candidates:
                    ct_findings.extend(candidates)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        if not ct_findings:
            log.debug("[R5] CT->PDNS pivot: no CT accepted findings")
            return
        from hledac.universal.runtime.acquisition_strategy import select_ct_domains_for_passivedns_pivot

        pivot_domains = select_ct_domains_for_passivedns_pivot(ct_findings, max_pivots=5)
        if not pivot_domains:
            log.debug("[R5] CT->PDNS pivot: no domains extracted from CT findings")
            return
        log.debug(f"[R5] CT->PDNS pivot: {len(pivot_domains)} domains: {pivot_domains[:3]}...")
        getattr(self, "_duckdb_store", None)
        pdns_results: list = []
        errors: list = []
        from hledac.universal.security.passive_dns import PassiveDNSOutcome
        from hledac.universal.security.passive_dns import call_lookup_passive_dns as _pdns_lookup

        async def _run_pdns_for_domain(domain: str) -> tuple[str, list[str], PassiveDNSOutcome]:
            """Run PassiveDNS for one domain, return (domain, ips, outcome)."""
            try:
                ips, outcome = await _pdns_lookup(domain)
                return (domain, ips, outcome)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                return (domain, [], None)

        try:
            gather_results = await safe_gather_ok(
                *[_run_pdns_for_domain(d) for d in pivot_domains], label="sprint_scheduler:19776"
            )
            for i, result in enumerate(gather_results):
                if isinstance(result, BaseException):
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    errors.append(f"domain_{i}_error:{result}")
                    continue
                domain, ips, outcome = result
                if outcome is not None:
                    pdns_results.append({"domain": domain, "ips": ips, "outcome": outcome, "pivot_source": "ct"})
                else:
                    errors.append(f"domain_{domain}_none")
        except asyncio.CancelledError:
            raise
        except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning("ct_to_passivedns_pivot gather failed: %s", _exc)
        ledger = getattr(self, "_nonfeed_ledger", None)
        if ledger is not None:
            for res in pdns_results:
                try:
                    domain = res.get("domain", "")
                    ips = res.get("ips", []) or []
                    outcome = res.get("outcome", None)
                    if outcome and hasattr(outcome, "result_count"):
                        count = getattr(outcome, "result_count", 0) or 0
                        if count > 0:
                            ledger.add_pivot_discovered(
                                pivot_type="ct_to_passivedns",
                                ioc_value=domain,
                                source_hint=f"ct_domain:{domain}",
                                reason=f"pdns_results={count}",
                            )
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
        if pdns_results:
            _sfos = list(getattr(self._result, "source_family_outcomes_list", []) or [])
            for res in pdns_results:
                outcome = res.get("outcome", None)
                if outcome is None:
                    continue
                _sfos.append(
                    {
                        "family": "passive_dns",
                        "lane": "PASSIVE_DNS",
                        "attempted": getattr(outcome, "attempted", True),
                        "accepted": getattr(outcome, "result_count", 0) or 0,
                        "terminal_state": "pivot_ct_domain",
                        "raw_count": 0,
                        "accepted_count": getattr(outcome, "result_count", 0) or 0,
                        "error": getattr(outcome, "error", None),
                        "timeout": getattr(outcome, "timeout", False),
                        "skipped": False,
                        "pivot_source": "ct",
                        "pivot_domains": pivot_domains,
                    }
                )
            self._result.source_family_outcomes_list = _sfos
        log.debug(f"[R5] CT->PDNS pivot done: {len(pdns_results)} domains with results, errors={len(errors)}")

    async def _run_bgp_advisory_sidecar(self) -> None:
        """

        Sprint F234: BGP IP-to-Org attribution advisory.



        Advisory-only sidecar -- runs after main sprint to enrich accepted

        findings with BGP/ASN intelligence. Fail-soft throughout: errors

        never crash the sprint.



        Flow:

          1. Extract domain/IP candidates from acquisition lane outcomes

          2. Query BGPView.io for ASN, org, prefix data

          3. Convert results to CanonicalFinding via BGPAdapter

          4. Record as source_family="bgp_advisory" in source_family_outcomes

        """
        try:
            from hledac.universal.intelligence.bgp_lane import BGPAdapter
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return
        try:
            session_provider = getattr(self, "_httpx_session_provider", None)
            if session_provider is None:

                async def _session_provider():
                    from hledac.universal.transport.session_pool import session_pool

                    return await session_pool.httpx()

            adapter = BGPAdapter(session_provider=session_provider)
            pdns_adapter = None
            if ENV.get_bool("HLEDAC_ENABLE_BGP_PDNS"):
                try:
                    from hledac.universal.intelligence.bgp_passive_dns_adapter import PassiveDNSAdapter

                    pdns_adapter = PassiveDNSAdapter()
                    pdns_adapter.set_session(await session_provider())
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            try:
                outcomes = getattr(self._result, "acquisition_lane_outcomes", ()) or ()
                domains: list[str] = []
                for outcome in outcomes:
                    if not getattr(outcome, "attempted", False):
                        continue
                    candidates = getattr(outcome, "candidate_findings", ()) or ()
                    for c in candidates:
                        ioc_type = getattr(c, "ioc_type", "") or ""
                        ioc_value = getattr(c, "ioc_value", "") or ""
                        if ioc_type in ("domain", "ip") and ioc_value:
                            domains.append(ioc_value)
                if not domains:
                    return
                seen = set()
                unique_domains = []
                for d in domains:
                    if d not in seen and len(unique_domains) < 20:
                        seen.add(d)
                        unique_domains.append(d)
                if not unique_domains:
                    return
                bgp_results = await adapter.enrich_org(unique_domains[0])
                if not bgp_results:
                    return
                store = getattr(self, "_duckdb_store", None)
                if store is not None:
                    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

                    findings: list[CanonicalFinding] = []
                    for r in bgp_results:
                        finding = CanonicalFinding(
                            finding_id=f"bgp-{r.prefix or r.asn}",
                            source_type=SourceType.BGP_INTELLIGENCE,
                            confidence=0.75,
                            query=self._query[:128],
                            ts=_time.time(),
                            payload_text=f"ASN={r.asn} org={r.asn_name} prefix={r.prefix} country={r.country_code}",
                            provenance=(f"asn:{r.asn}", f"source:{r.source}"),
                        )
                        findings.append(finding)
                    if findings:
                        _ = await self._gate_then_ingest_and_accumulate(store, findings, sprint_id=self.sprint_id or "")
                        self._result.bgp_advisory_findings_produced = len(findings)
                if pdns_adapter is not None and unique_domains and (store is not None):

                    async def _query_one(domain: str) -> list:
                        try:
                            recs = await pdns_adapter.query_pdns(domain)
                            return [
                                rec.to_canonical_finding(self._query)
                                for rec in recs
                                if rec.to_canonical_finding(self._query)
                            ]
                        except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                            return []

                    _pdns_tasks = [_query_one(d) for d in unique_domains[:5]]
                    _pdns_results = await safe_gather_ok(*_pdns_tasks, label="sprint_scheduler:pdns_query")
                    pdns_findings = []
                    for _pr in _pdns_results:
                        if _pr and isinstance(_pr, list):
                            pdns_findings.extend(_pr)
                    if pdns_findings:
                        _ = await self._gate_then_ingest_and_accumulate(
                            store, pdns_findings, sprint_id=self.sprint_id or ""
                        )
                        self._result.pdns_advisory_findings_produced = len(pdns_findings)
                _sfos = list(getattr(self._result, "source_family_outcomes_list", []) or [])
                _sfos.append(
                    {
                        "family": "bgp_advisory",
                        "lane": "BGP_ADVISORY",
                        "attempted": True,
                        "accepted": len(bgp_results),
                        "terminal_state": "advisory_bgp",
                        "raw_count": len(bgp_results),
                        "accepted_count": len(bgp_results),
                        "error": None,
                        "timeout": False,
                        "skipped": False,
                    }
                )
                self._result.source_family_outcomes_list = _sfos
            finally:
                await adapter.close()
                if pdns_adapter is not None:
                    try:
                        await pdns_adapter._session.aclose()
                    except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                        pass
        except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
            pass

    async def _run_ti_feed_sidecar(self) -> None:
        """
        F252: TI feed advisory sidecar (NVD + CISA KEV).

        Fetches structured threat-intel feeds in parallel using safe_gather_ok.
        Adapters are registered via source_registry; dispatches NvdApiAdapter
        and CisaKevAdapter in parallel with bounded concurrency.
        Fail-soft throughout: errors never crash the sprint.
        """
        try:
            from hledac.universal.discovery.ti_feed_adapter import CisaKevAdapter, NvdApiAdapter
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return
        try:
            nvd_adapter = NvdApiAdapter()
            cisa_adapter = CisaKevAdapter()
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return
        try:
            nvd_task = nvd_adapter.fetch_recent(limit=50)
            cisa_task = cisa_adapter.fetch_recent(limit=50)
            results = await safe_gather_ok(nvd_task, cisa_task, label="sprint_scheduler:_run_ti_feed_sidecar")
            accepted_count = 0
            for result in results:
                if isinstance(result, tuple) and result:
                    accepted_count += len(result)
            if accepted_count > 0:
                self._emit_source_family_event(
                    "ti_feed_advisory",
                    "findings",
                    count=accepted_count,
                    reason=f"ti_feed_advisory: NVD+CISA KE collected {accepted_count} entries",
                )
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass

    async def _run_wayback_cdx_deep_sidecar(self) -> None:
        """

        Sprint F234: WaybackCDX deep search advisory.



        Advisory-only sidecar -- runs after main sprint to discover archived

        URLs for accepted domains. Fail-soft throughout.



        Flow:

          1. Extract domains from acquisition lane outcomes

          2. Query Wayback CDX for archived URLs (deep domain discovery)

          3. Convert results to CanonicalFinding via WaybackCDXDeepSearch

          4. Record as source_family="wayback_cdx_advisory" in source_family_outcomes

        """
        try:
            from hledac.universal.intelligence.wayback_cdx import WaybackCDXDeepSearch
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return
        try:
            session_provider = getattr(self, "_httpx_session_provider", None)
            if session_provider is None:

                async def _session_provider():
                    from hledac.universal.transport.session_pool import session_pool

                    return await session_pool.httpx()

            searcher = WaybackCDXDeepSearch(session_provider=session_provider)
            try:
                outcomes = getattr(self._result, "acquisition_lane_outcomes", ()) or ()
                domains: list[str] = []
                for outcome in outcomes:
                    if not getattr(outcome, "attempted", False):
                        continue
                    candidates = getattr(outcome, "candidate_findings", ()) or ()
                    for c in candidates:
                        ioc_type = getattr(c, "ioc_type", "") or ""
                        ioc_value = getattr(c, "ioc_value", "") or ""
                        if ioc_type == "domain" and ioc_value:
                            domains.append(ioc_value)
                if not domains:
                    return
                seen = set()
                unique_domains = []
                for d in domains:
                    if d not in seen and len(unique_domains) < 10:
                        seen.add(d)
                        unique_domains.append(d)
                if not unique_domains:
                    return
                result = await searcher.search(
                    domains_or_urls=unique_domains, match_type="domain", limit_per_domain=100, concurrency=2
                )
                if not result.results:
                    return
                findings = result.to_findings(query=self._query, sprint_id=self.sprint_id or "")
                if not findings:
                    return
                store = getattr(self, "_duckdb_store", None)
                if store is not None:
                    ingested = await self._gate_then_ingest_and_accumulate(
                        store, findings, sprint_id=self.sprint_id or ""
                    )
                    stored = sum((1 for r in ingested if isinstance(r, dict) and r.get("accepted")))
                else:
                    stored = 0
                _sfos = list(getattr(self._result, "source_family_outcomes_list", []) or [])
                _sfos.append(
                    {
                        "family": "wayback_cdx_advisory",
                        "lane": "WAYBACK_CDX_ADVISORY",
                        "attempted": True,
                        "accepted": stored,
                        "terminal_state": "advisory_wayback_cdx",
                        "raw_count": len(result.results),
                        "accepted_count": stored,
                        "error": None,
                        "timeout": False,
                        "skipped": False,
                    }
                )
                self._result.source_family_outcomes_list = _sfos
            finally:
                await searcher.close()
        except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
            pass

    MAX_SOURCE_HEALTH_ENTRIES: Final[int] = 100
    _POSTURE_ORDER: Final[dict[str, int]] = {"hot": 0, "warm": 1, "lukewarm": 2, "marginal": 3, "cold": 4, "unknown": 5}
    MAX_BREAKER_DOMAINS: Final[int] = MAX_TRACKED_DOMAINS

    def _get_source_health_summary(self) -> dict:
        """

        F206I: Build a bounded source health summary from per-source economics.



        Reads _source_economics (in-memory, per-sprint) and returns a

        compact summary dict for the diagnostic report. Non-persisting.



        Bounds:

        - MAX_SOURCE_HEALTH_ENTRIES=100 (most-healthy first)

        - Each entry is a small dict with posture and cooldown info



        Fail-soft: returns empty dict on any error.



        GHOST_INVARIANTS:

        - No asyncio.gather / _check_gathered (sync method)

        - No asyncio.run() or loop.run_until_complete()

        - No model/MLX imports

        - No canonical write path (read-only)

        """
        try:
            if not self._source_economics:
                return {}
            sorted_sources = sorted(
                self._source_economics.values(),
                key=lambda e: (self._POSTURE_ORDER.get(e.recent_health_posture, 5), -e.last_signal_cycle),
            )
            entries = []
            for econ in sorted_sources[: self.MAX_SOURCE_HEALTH_ENTRIES]:
                entries.append(
                    {
                        "source": econ.source,
                        "posture": econ.recent_health_posture,
                        "last_signal_cycle": econ.last_signal_cycle,
                        "silent_streak": econ.silent_streak,
                        "in_cooldown": econ.cooldown_until_cycle is not None,
                    }
                )
            total_tracked = len(self._source_economics)
            return {"entries": entries, "total_tracked": total_tracked, "max_entries": self.MAX_SOURCE_HEALTH_ENTRIES}
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return {}

    def _get_graph_signal(self) -> dict:
        """

        F198A: Read graph signal at teardown without blocking sprint.



        Returns graph node/edge stats as a dict, or empty dict on error.

        Non-blocking: called inside _build_diagnostic_report which is already

        in the export teardown path (not on the critical sprint path).

        """
        try:
            from hledac.universal.knowledge import graph_service

            stats = graph_service.graph_stats()
            if stats:
                return {
                    "graph_nodes": stats.get("nodes", 0),
                    "graph_edges": stats.get("edges", 0),
                    "graph_pgq_available": stats.get("pgq_available", False),
                }
        except Exception:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
            pass
        return {}

    MAX_PIVOT_GRAPH_STATS_NODES: int = 500

    def _get_pivot_graph_stats_for_planning(self) -> dict:
        """

        F238D: Build structured graph_stats dict for PivotPlanner scoring.



        Called during nonfeed prelude (before advisory runner) to populate

        graph_stats with {nodes, edges, domains, connected_iocs, node_degrees}

        so that _score_pivot_domain and _score_pivot_graph can apply degree penalties

        and novelty checks.



        Returns empty dict (fail-soft) if graph unavailable or query fails.

        No network, no model, no DuckDB heavy scans -- only bounded in-memory

        aggregation over already-persisted graph data.



        GHOST_INVARIANTS:

        - No asyncio.run() or loop.run_until_complete()

        - No model/MLX imports

        - No network calls

        - Bounded: MAX_PIVOT_GRAPH_STATS_NODES=500

        """
        try:
            from hledac.universal.knowledge import graph_service

            stats = graph_service.graph_stats()
            nodes = stats.get("nodes", 0) if stats else 0
            edges = stats.get("edges", 0) if stats else 0
            domains: list[str] = []
            node_degrees: dict[str, int] = {}
            confidence_by_node: dict[str, float] = {}
            source_count_by_node: dict[str, int] = {}
            if not ENV.get_bool("HLEDAC_ENABLE_GRAPH_ANALYSIS"):
                logger.debug("[winddown] Graph analytics skipped (HLEDAC_ENABLE_GRAPH_ANALYSIS=0)")
            else:
                try:
                    summary = graph_service.graph_analytics_summary(top_k=500)
                    if summary.get("analytics_available"):
                        for entity in summary.get("top_central_entities", [])[:500]:
                            val = entity.get("value", "")
                            deg = entity.get("degree", 0)
                            conf = entity.get("max_confidence", 0.5)
                            if val and deg > 0:
                                domains.append(val)
                                node_degrees[val] = deg
                                confidence_by_node[val] = max(0.0, min(1.0, conf))
                        _conf_src: dict[str, int] = {}
                        for entity in summary.get("top_central_entities", [])[:500]:
                            val = entity.get("value", "")
                            if val:
                                _conf_src[val] = _conf_src.get(val, 0) + 1
                        source_count_by_node = _conf_src
                except Exception:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
                    pass
            return {
                "nodes": nodes,
                "edges": edges,
                "domains": domains,
                "connected_iocs": set(),
                "node_degrees": node_degrees,
                "confidence_by_node": confidence_by_node,
                "source_count_by_node": source_count_by_node,
            }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return {}

    MAX_WINDUP_SCORECARD_KEYS: int = 32

    def _get_windup_scorecard(self) -> dict:
        """

        F206E: Extract read-only windup scorecard fields from active pipeline data.



        Reads bounded diagnostic fields from windup_engine.py scorecard WITHOUT

        activating the dormant run_windup() path. No model load, no GNN import.



        Safe read-only sources:

        - Circuit breaker states (transport.circuit_breaker)

        - Phase durations (from result timing fields)

        - Graph stats (from graph_service, already via _get_graph_signal)

        - Peak RSS (from result.peak_rss_gib or psutil)



        Fail-soft: returns empty dict on any error.



        GHOST_INVARIANTS:

        - No asyncio.run() or loop.run_until_complete()

        - No model/MLX imports on hot path

        - No GNN inference

        - Bounded: MAX_WINDUP_SCORECARD_KEYS=32

        """
        try:
            scorecard: dict = {}
            try:
                cb_states = get_all_breaker_states()
                if cb_states:
                    open_domains = {d: s for d, s in cb_states.items() if s in ("open", "half_open")}
                    if open_domains:
                        scorecard["cb_open_domains"] = open_domains
                    scorecard["cb_tracked_count"] = len(cb_states)
            except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.warning(
                    "sprint %s: scorecard cb_tracked_count failed -- %s: %s",
                    getattr(self._result, "sprint_id", "?"),
                    type(_exc).__name__,
                    _exc,
                )
            phase_durations: dict = {}
            if self._result.pre_loop_elapsed_s is not None:
                phase_durations["warmup_s"] = round(self._result.pre_loop_elapsed_s, 2)
            if (
                self._result.entered_active_at_monotonic is not None
                and self._result.first_cycle_started_at_monotonic is not None
            ):
                active_dur = round(
                    self._result.first_cycle_started_at_monotonic - self._result.entered_active_at_monotonic, 2
                )
                phase_durations["active_s"] = max(0.0, active_dur)
            if phase_durations:
                scorecard["phase_durations"] = phase_durations
            graph_signal = self._get_graph_signal()
            if graph_signal:
                scorecard["graph_nodes"] = graph_signal.get("graph_nodes", 0)
                scorecard["graph_edges"] = graph_signal.get("graph_edges", 0)
                scorecard["graph_pgq_available"] = graph_signal.get("graph_pgq_available", False)
            if self._result.peak_rss_gib > 0:
                scorecard["peak_rss_mb"] = round(self._result.peak_rss_gib * 1024, 1)
            _total_accepted = self._finding_count
            if _total_accepted > 0:
                scorecard["accepted_findings"] = _total_accepted
            sidecar_counts: dict = {}
            if self._result.identity_findings_produced > 0:
                sidecar_counts["identity"] = self._result.identity_findings_produced
            if self._result.exposure_findings_produced > 0:
                sidecar_counts["exposure"] = self._result.exposure_findings_produced
            if self._result.timeline_findings_produced > 0:
                sidecar_counts["timeline"] = self._result.timeline_findings_produced
            if self._result.leak_findings_produced > 0:
                sidecar_counts["leak"] = self._result.leak_findings_produced
            if self._result.evidence_triage_findings_count > 0:
                sidecar_counts["evidence_triage"] = self._result.evidence_triage_findings_count
            if self._result.forensics_enriched_ct_findings > 0:
                sidecar_counts["forensics"] = self._result.forensics_enriched_ct_findings
            if self._result.multimodal_enriched_findings > 0:
                sidecar_counts["multimodal"] = self._result.multimodal_enriched_findings
            if sidecar_counts:
                scorecard["sidecar_findings"] = sidecar_counts
            if self._result.branch_timeout_count > 0:
                scorecard["branch_timeouts"] = self._result.branch_timeout_count
                _gov = getattr(self, "_governor", None)
                if _gov is not None:
                    scorecard["ema_branch_pressure"] = round(_gov.ema_branch_pressure, 3)
            if self._result.budget_violations > 0:
                scorecard["budget_violations"] = self._result.budget_violations
            _se = getattr(self, "_synthesis_engine", None) or "unknown"
            if _se != "unknown":
                scorecard["synthesis_engine_used"] = _se
            if self._result.accepted_findings > 0 and self._result.sprint_elapsed_s > 0:
                scorecard["findings_per_minute"] = round(
                    self._result.accepted_findings / (self._result.sprint_elapsed_s / 60), 2
                )
            if self._result.ioc_density > 0:
                scorecard["ioc_density"] = round(self._result.ioc_density, 4)
            if len(scorecard) > self.MAX_WINDUP_SCORECARD_KEYS:
                priority_keys = [
                    "cb_open_domains",
                    "phase_durations",
                    "graph_nodes",
                    "graph_edges",
                    "peak_rss_mb",
                    "accepted_findings",
                    "sidecar_findings",
                    "branch_timeouts",
                    "budget_violations",
                    "graph_pgq_available",
                    "cb_tracked_count",
                ]
                pruned: dict = {}
                for k in priority_keys:
                    if k in scorecard:
                        pruned[k] = scorecard[k]
                        if len(pruned) >= self.MAX_WINDUP_SCORECARD_KEYS:
                            break
                scorecard = pruned
            return scorecard
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return {}

    def _get_circuit_breaker_summary(self) -> dict:
        """

        F206I: Build a bounded circuit breaker state summary for the diagnostic report.



        Reads the shared domain circuit breaker registry (get_all_breaker_snapshots)

        and returns a compact summary. Non-persisting, in-memory only.



        Bounds:

        - MAX_TRACKED_DOMAINS=500 (from circuit_breaker module)

        - MAX_BREAKER_DOMAINS=500 (local alias)

        - Each snapshot is a small dict: domain, state, failure_count, retry_after_s



        Fail-soft: returns empty dict on any error.



        GHOST_INVARIANTS:

        - No asyncio.gather / _check_gathered (sync method)

        - No asyncio.run() or loop.run_until_complete()

        - No canonical write path (read-only)

        - Circuit breaker itself does not persist

        """
        try:
            snapshots = get_all_breaker_snapshots()
            if not snapshots:
                return {"total_tracked": 0, "open_count": 0, "half_open_count": 0}
            open_count = sum((1 for s in snapshots if s.state == "open"))
            half_open_count = sum((1 for s in snapshots if s.state == "half_open"))
            entries = []
            for snap in snapshots[: self.MAX_BREAKER_DOMAINS]:
                entries.append(
                    {
                        "domain": snap.domain,
                        "state": snap.state,
                        "failure_count": snap.failure_count,
                        "last_failure_kind": snap.last_failure_kind,
                        "recovery_timeout_s": round(snap.recovery_timeout_s, 1),
                    }
                )
            return {
                "total_tracked": len(snapshots),
                "open_count": open_count,
                "half_open_count": half_open_count,
                "entries": entries,
                "max_entries": self.MAX_BREAKER_DOMAINS,
            }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return {}

    def _build_work_items(self, sources: Sequence[str]) -> list[SourceWork]:
        """Build and tier-sort work items from source list.

        Sprint F228G: tier resolution falls back to _DEFAULT_SOURCE_TIER_MAP
        before defaulting to SourceTier.OTHER. The five canonical structured
        TI feeds (cisa_kev, threatfox_ioc, urlhaus_recent, feodo_ip,
        openphish_feed) are mapped to STRUCTURED_TI so they survive prune
        mode and produce real work each cycle.
        """
        items = []
        for url in sources:
            tier = self._config.tier_of(url)
            if tier == SourceTier.OTHER and url in _DEFAULT_SOURCE_TIER_MAP:
                tier = _DEFAULT_SOURCE_TIER_MAP[url]
            items.append(
                SourceWork(feed_url=url, source=url, tier=tier, max_entries=self._config.max_entries_per_cycle)
            )
        items.sort(key=lambda w: _TIER_ORDER.index(w.tier))
        return items

    def _prune_work_items(self, items: list[SourceWork]) -> list[SourceWork]:
        """Drop ARCHIVE and OTHER tier items when in prune mode."""
        return [w for w in items if w.tier not in (SourceTier.ARCHIVE, SourceTier.OTHER)]

    async def _enrich_findings_multimodal(self, findings: list) -> None:
        """
        Enrich PDF/image findings with multimodal analysis before storage.

        Fail-safe: enrichment errors are silent -- never crash or abort the sprint.
        Enrichment is best-effort: absence of multimodal data is not an error.
        """
        if not findings:
            return
        enricher = self._multimodal_enricher
        lmdb_env = self._multimodal_lmdb_env
        if enricher is None or lmdb_env is None:
            return
        enriched_pairs: list[tuple[bytes, bytes]] = []

        def _serialize(r):
            return _msgspec_encode(r)

        def _sync_enrich_and_serialize(finding) -> tuple[str, bytes] | None:
            """Sync wrapper: enrich + serialize in thread pool. Returns (fid, payload) or None."""
            try:
                result = enricher.enrich(finding)
                if result is not None:
                    fid = getattr(finding, "finding_id", None)
                    if fid:
                        payload_bytes = _serialize(result)
                        if isinstance(payload_bytes, str):
                            payload_bytes = payload_bytes.encode()
                        return (fid, payload_bytes)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
            return None

        try:
            # F350M-R: Increased from 3 to 16 — M1 8GB handles higher concurrency
            # for I/O-bound enrichment tasks (LMDB write via asyncio.to_thread)
            semaphore = asyncio.Semaphore(16)

            async def enrich_one(finding) -> None:
                nonlocal enriched_pairs
                async with semaphore:
                    try:
                        fid, payload = await asyncio.to_thread(_sync_enrich_and_serialize, finding)
                        if fid is not None and payload is not None:
                            enriched_pairs.append((fid.encode(), payload))
                        self._result.multimodal_enriched_findings += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        pass

            await safe_gather(*[enrich_one(f) for f in findings], label="multimodal_enrichment", logger_instance=log)
            if enriched_pairs:
                written = putmulti_bounded(lmdb_env, enriched_pairs, overwrite=True)
                log.debug("multimodal LMDB bulk-write: %d/%d", written, len(enriched_pairs))
        except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            pass

    def _feed_dominance_should_fetch(self, work: _FeedWorkItem, nonfeed_terminal: bool) -> tuple[bool, str]:
        """F216E+F227D: Determine if a feed source should be fetched given current budget state.



        F227D: Added mission_intent and nonfeed_unresolved to support mission-aware cap.

        F230D: Added acquisition_profile for nonfeed_diagnostic profile cap.



        Returns (should_fetch, reason):

          - (True, "")       -- source should run normally

          - (False, reason)  -- source should be skipped due to budget cap

        """
        budget = None
        mission_intent = None
        acquisition_profile = None
        if self._acquisition_plan is not None:
            budget = getattr(self._acquisition_plan, "feed_dominance_budget", None)
            mission_intent = getattr(self._acquisition_plan, "mission_intent", None)
            nd = getattr(self._acquisition_plan, "nonfeed_plan_debug", None)
            if nd is not None:
                acquisition_profile = getattr(nd, "acquisition_profile", None)
        nonfeed_unresolved = not nonfeed_terminal
        if budget is None:
            return (True, "")
        if mission_intent is not None and nonfeed_unresolved:
            mission_cap_reason = budget.cap_feeding(
                feed_accepted_so_far=self._result.accepted_findings,
                nonfeed_accepted_so_far=self._result.lane_ct_accepted_findings
                + self._result.lane_wayback_accepted_findings
                + self._result.lane_pdns_accepted_findings
                + self._result.lane_blockchain_accepted_findings,
                feed_per_source=get_sprint_ctx().feed_accepted_per_source,
                mission_intent=mission_intent,
                nonfeed_unresolved=nonfeed_unresolved,
                acquisition_profile=acquisition_profile,
            )
            if mission_cap_reason[0]:
                if mission_cap_reason[1].startswith("feed_cap_active:mission:"):
                    self._result.feed_budget_reason = mission_cap_reason[1]
                    if self._acquisition_plan is not None and self._acquisition_plan.nonfeed_plan_debug is not None:
                        nd = self._acquisition_plan.nonfeed_plan_debug
                        nd.mission_feed_cap_reason = mission_cap_reason[1]
                        nd.feed_cap_applied_by_mission = True
                        nd.feed_cap_mission_intent = mission_intent
                return (False, mission_cap_reason[1])
        if acquisition_profile == AcquisitionProfile.NONFEED_DIAGNOSTIC and nonfeed_unresolved:
            profile_cap_reason = budget.cap_feeding(
                feed_accepted_so_far=self._result.accepted_findings,
                nonfeed_accepted_so_far=self._result.lane_ct_accepted_findings
                + self._result.lane_wayback_accepted_findings
                + self._result.lane_pdns_accepted_findings
                + self._result.lane_blockchain_accepted_findings,
                feed_per_source=self._feed_accepted_per_source,
                mission_intent=mission_intent
                if mission_intent
                else infer_mission_intent(self._acquisition_plan.query)
                if self._acquisition_plan and self._acquisition_plan.query
                else "unknown",
                nonfeed_unresolved=nonfeed_unresolved,
                acquisition_profile=acquisition_profile,
            )
            if profile_cap_reason[0]:
                self._result.feed_budget_reason = profile_cap_reason[1]
                if self._acquisition_plan is not None and self._acquisition_plan.nonfeed_plan_debug is not None:
                    nd = self._acquisition_plan.nonfeed_plan_debug
                    nd.feed_cap_reason = profile_cap_reason[1]
                    nd.feed_cap_applied_by_mission = True
                return (False, profile_cap_reason[1])
        if not budget.is_active():
            return (True, "")
        if nonfeed_terminal:
            return (True, "")
        if self._feed_budget_triggered:
            return (False, "feed_budget_triggered:global_suppressed")
        budget_reason = budget.cap_feeding(
            feed_accepted_so_far=self._result.accepted_findings,
            nonfeed_accepted_so_far=self._result.lane_ct_accepted_findings
            + self._result.lane_wayback_accepted_findings
            + self._result.lane_pdns_accepted_findings
            + self._result.lane_blockchain_accepted_findings,
            feed_per_source=get_sprint_ctx().feed_accepted_per_source,
            nonfeed_unresolved=nonfeed_unresolved,
            acquisition_profile=acquisition_profile,
        )
        if budget_reason[0]:
            return (False, budget_reason[1])
        return (True, "")

    def _feed_dominance_record_result(self, feed_url: str, accepted_count: int, suppressed: bool, reason: str) -> None:
        """F216E: Record feed result into budget telemetry.



        F230D: Also records nonfeed_budget telemetry when nonfeed_diagnostic profile active.

        """
        if accepted_count > 0:
            ctx = get_sprint_ctx()
            ctx.feed_accepted_per_source[feed_url] = ctx.feed_accepted_per_source.get(feed_url, 0) + accepted_count
        if suppressed:
            self._result.feed_suppressed_by_budget += accepted_count
            if "nonfeed_profile" in reason:
                self._result.feed_suppressed_by_nonfeed_budget += accepted_count
                self._result.feed_suppression_count += 1
                self._result.feed_suppression_reason = reason
        if not self._feed_budget_triggered and reason:
            self._feed_budget_triggered = True
            self._result.feed_budget_active = True
            self._result.feed_budget_reason = reason
            ctx = get_sprint_ctx()
            self._result.feed_accepted_before_cap = self._result.accepted_findings
            self._result.feed_budget_per_source = dict(ctx.feed_accepted_per_source)
            top = sorted(ctx.feed_accepted_per_source.items(), key=lambda x: x[1], reverse=True)[:10]
            self._result.top_feed_source_counts = tuple(top)
            budget = None
            if self._acquisition_plan is not None:
                budget = getattr(self._acquisition_plan, "feed_dominance_budget", None)
            if budget and budget.max_feed_per_source > 0:
                for src, cnt in top:
                    if cnt >= budget.max_feed_per_source:
                        self._result.max_per_source_applied = src
                        break
            if "nonfeed_profile" in reason:
                self._result.nonfeed_budget_active = True
                nd = getattr(self._acquisition_plan, "nonfeed_plan_debug", None) if self._acquisition_plan else None
                if nd is not None:
                    self._result.nonfeed_budget_expected_lanes = getattr(nd, "nonfeed_profile_expected_lanes", ())
                    _expected = list(self._result.nonfeed_budget_expected_lanes)
                    _terminal = []
                    _unresolved = []
                    for lane in _expected:
                        _count = getattr(self._result, f"lane_{lane.lower()}_accepted_findings", 0)
                        if _count > 0:
                            _terminal.append(lane)
                        else:
                            _unresolved.append(lane)
                    self._result.nonfeed_budget_terminal_lanes = tuple(_terminal)
                    self._result.nonfeed_budget_unresolved_lanes = tuple(_unresolved)

    async def _enrich_ct_findings_forensics(self, findings: list) -> None:
        """
        Enrich CT findings with forensics analysis before storage.

        Fail-safe: enrichment errors are silent -- never crash or abort the sprint.
        Enrichment is best-effort: absence of forensics data is not an error.
        """
        if not findings:
            return
        enricher = self._forensics_enricher
        lmdb_env = self._forensics_lmdb_env
        if enricher is None or lmdb_env is None:
            return
        enriched_pairs: list[tuple[bytes, bytes]] = []

        def _serialize(r):
            return _msgspec_encode(r)

        def _sync_enrich_and_serialize(finding) -> tuple[str, bytes] | None:
            """Sync wrapper: enrich + serialize in thread pool. Returns (fid, payload) or None."""
            try:
                result = enricher.enrich(finding)
                if result is not None:
                    fid = getattr(finding, "finding_id", None)
                    if fid:
                        payload_bytes = _serialize(result)
                        if isinstance(payload_bytes, str):
                            payload_bytes = payload_bytes.encode()
                        return (fid, payload_bytes)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
            return None

        try:
            # F350M-R: Increased from 3 to 16 — M1 8GB handles higher concurrency
            # for I/O-bound enrichment tasks (LMDB write via asyncio.to_thread)
            semaphore = asyncio.Semaphore(16)

            async def enrich_one(finding) -> None:
                nonlocal enriched_pairs
                async with semaphore:
                    try:
                        fid, payload = await asyncio.to_thread(_sync_enrich_and_serialize, finding)
                        if fid is not None and payload is not None:
                            enriched_pairs.append((fid.encode(), payload))
                        self._result.forensics_enriched_ct_findings += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        pass

            await safe_gather(*[enrich_one(f) for f in findings], label="forensics_enrichment", logger_instance=log)
            if enriched_pairs:
                written = putmulti_bounded(lmdb_env, enriched_pairs, overwrite=True)
                log.debug("forensics LMDB bulk-write: %d/%d", written, len(enriched_pairs))
        except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            pass

    def _process_result(self, feed_url: str, result) -> None:
        """Accumulate result stats and dedup."""
        ctx = get_sprint_ctx()
        ctx.entries_per_source[feed_url] = ctx.entries_per_source.get(feed_url, 0) + result.fetched_entries
        ctx.hits_per_source[feed_url] = ctx.hits_per_source.get(feed_url, 0) + result.matched_patterns
        self._result.entries_per_source[feed_url] = ctx.entries_per_source[feed_url]
        self._result.hits_per_source[feed_url] = ctx.hits_per_source[feed_url]
        self._result.total_pattern_hits += result.matched_patterns
        _tel = result.telemetry
        self._result.entries_seen += getattr(_tel, "entries_seen", 0) if _tel else 0
        self._result.entries_scanned += getattr(_tel, "entries_scanned", 0) if _tel else 0
        self._result.entries_with_hits += getattr(_tel, "entries_with_hits", 0) if _tel else 0
        self._result.findings_built_pre_store += getattr(_tel, "findings_built_pre_store", 0) if _tel else 0
        _sig = getattr(_tel, "signal_stage", None) if _tel else None
        if _sig and _sig != "unknown":
            self._result.signal_stage = _sig
        self._result.accepted_findings += result.accepted_findings
        if result.accepted_findings > 0:
            self._emit_source_family_event(family="FEED", event="accepted", count=result.accepted_findings)
        self._finding_count += result.accepted_findings
        if _tel is not None and hasattr(_tel, "feed_economics_verdict"):
            verdict = _tel.feed_economics_verdict
            if verdict and isinstance(verdict, (list, tuple)) and (len(verdict) == 5):
                self._feed_verdicts.append(tuple(verdict))
        _zsr = getattr(_tel, "zero_signal_reason", None) if _tel else None
        _stage = getattr(_tel, "signal_stage", "unknown") if _tel else "unknown"
        if _zsr:
            self._result.feed_zero_yield_detected = True
            match _zsr:
                case "empty_fetch":
                    self._result.feed_inaccessible_detected = True
                case "content_empty":
                    self._result.feed_content_empty_detected = True
                case "no_pattern_hits_with_content":
                    self._result.feed_no_pattern_with_content = True
                case "findings_build_loss":
                    self._result.findings_build_loss_detected = True
                    self._result.feed_no_signal_sources.append(feed_url)
            if len(self._result.feed_no_signal_sources) < 20:
                if feed_url not in self._result.feed_no_signal_sources:
                    self._result.feed_no_signal_sources.append(feed_url)
        if hasattr(result, "matched_patterns") and result.matched_patterns > 0:
            finding_entry = {
                "type": "pattern_hit",
                "source": feed_url,
                "matched_patterns": result.matched_patterns,
                "accepted_findings": result.accepted_findings,
                "severity": "medium",
                "confidence": 0.6,
                "description": f"{result.matched_patterns} pattern hits from {feed_url}",
            }
            if len(self._all_findings) < self._MAX_FINDINGS_PER_SPRINT:
                self._all_findings.append(finding_entry)
        self._update_source_economics(feed_url, result, self._result.cycles_started)
        if len(self._source_quality_feedback) < 200:
            fb = self._source_quality_feedback.setdefault(feed_url, {"fetched": 0, "accepted": 0})
            fb["fetched"] = fb.get("fetched", 0) + getattr(result, "fetched_entries", 0)
            fb["accepted"] = fb.get("accepted", 0) + getattr(result, "accepted_findings", 0)
        if self._prefetch_oracle is not None:
            try:
                self._prefetch_oracle.record_outcome(
                    feed_url=feed_url,
                    fetched=getattr(result, "fetched_entries", 0),
                    accepted=getattr(result, "accepted_findings", 0),
                    cycle=self._result.cycles_started,
                    seen_new_urls=getattr(result, "matched_patterns", 0),
                )
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        self._feed_dominance_record_result(
            feed_url=feed_url,
            accepted_count=result.accepted_findings,
            suppressed=getattr(result, "error", "") == "feed_budget_cap_suppressed",
            reason=getattr(result, "feed_budget_cap_reason", ""),
        )

    async def _load_dedup(self) -> None:
        """Load existing hashes from LMDB at BOOT. Idempotent. Non-blocking via to_thread."""
        db_path = _get_dedup_lmdb_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        def _load_sync() -> tuple[Any, set[str], int]:
            """Synchronous LMDB load — runs in thread pool to avoid event-loop blocking."""
            env = None
            seen: set[str] = set()
            count = 0
            try:
                from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard

                env = open_lmdb_with_guard(db_path, map_size=100 * 1024 * 1024, max_dbs=1)
                with env.begin() as txn:
                    cursor = txn.cursor()
                    for key, _ in cursor:
                        seen.add(key.decode())
                        count += 1
                if len(seen) > 500000:
                    seen = set(sorted(seen)[-400000:])
                    log.warning(f"Dedup set trimmed to 400k entries (was {count})")
                log.info(f"Dedup LMDB loaded: {count} existing hashes")
                return (env, seen, count)
            except Exception as exc:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                log.warning(f"Dedup LMDB open failed: {exc} -- continuing without persistence")
                return (None, set(), 0)

        env, seen, _count = await asyncio.to_thread(_load_sync)
        self._dedup_env = env
        self._dedup_seen = seen

    async def _ensure_dedup_loaded(self) -> None:
        """Block until lazy dedup load completes. Call at first cycle entry."""
        if self._dedup_loading_task is None:
            return
        try:
            await self._dedup_loading_task
            self._dedup_loading_task = None
            self._result.dedup_preload_count = len(self._dedup_seen) if self._dedup_seen is not None else 0
        except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning(f"Dedup lazy load failed: {_e} -- continuing without dedup")
            self._dedup_loading_task = None
            self._dedup_seen = set()
            self._dedup_env = None

    async def _flush_dedup(self) -> None:
        """Flush in-memory hashes to LMDB. Called at WINDUP."""
        if self._dedup_env is None or not self._dedup_seen:
            return
        try:
            ts_bytes = struct.pack("d", _time.time())
            written = putmulti_bounded(
                self._dedup_env, [(k.encode(), ts_bytes) for k in self._dedup_seen], overwrite=True
            )
            log.info(f"Dedup flushed: {written}/{len(self._dedup_seen)} hashes")
        except Exception as exc:  # noqa: BLE001 — best-effort; export/write failure; non-critical
            log.warning(f"Dedup flush failed: {exc}")

    async def _run_ioc_cooccurrence_sidecar(self, query: str, duckdb_store: Any) -> None:
        """
        Issue 4.1: Run IOC co-occurrence analysis on accumulated findings.

        Wired in WINDUP phase — runs after all acquisition lanes complete so the
        full finding set is available. Uses:
          - Rust engine (compute_cooccurrence_edges_py) via asyncio.to_thread()
          - msgspec.to_builtins() for cheap serialization

        Architecture:
          finding_pipeline (async enrich+store) ∥ live_public_pipeline ∥ IOCooccurrenceMiner

        M1 8GB: asyncio.to_thread() runs Rust engine without blocking event loop.
        No ProcessPoolExecutor — rayon CPU pool handles multi-core parallelism.
        """
        try:
            from hledac.universal.pipeline.ioc_cooccurrence_miner import IOCooccurrenceMiner
        except Exception as e:  # noqa: BLE001 — best-effort; lock acquisition failure; non-critical
            logger.debug("[IOC] IOCooccurrenceMiner import failed: %s", e)
            return
        findings: list[Any] = []
        try:
            if hasattr(self, "_all_findings") and self._all_findings:
                findings = self._all_findings
            elif duckdb_store is not None:
                if hasattr(duckdb_store, "get_recent_findings"):
                    findings = await duckdb_store.get_recent_findings(limit=5000)
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            logger.debug("[IOC] Failed to get findings for co-occurrence: %s", e)
            return
        if not findings:
            logger.debug("[IOC] No findings for co-occurrence analysis")
            return
        try:
            if self._ioc_cooccurrence_miner is None:
                self._ioc_cooccurrence_miner = IOCooccurrenceMiner()
            edges = await self._ioc_cooccurrence_miner.analyze(findings)
            if edges:
                logger.info("[IOC] Co-occurrence: %d edges from %d findings", len(edges), len(findings))
                self._result.ioc_cooccurrence_edges = len(edges)
        except Exception as e:  # noqa: BLE001 — best-effort; logging failure; non-critical
            logger.debug("[IOC] Co-occurrence analysis failed: %s", e)

    async def _run_synthesis_sidecar(self, query: str, duckdb_store: Any, lifecycle: Any) -> None:
        """Sprint F259: Run SynthesisRunner in WINDUP phase."""
        if not ENV.get_bool("HLEDAC_ENABLE_HERMES_SYNTHESIS"):
            log.debug("[F259] Synthesis skipped -- HLEDAC_ENABLE_SYNTHESIS != '1'")
            return
        try:
            from hledac.universal.utils.uma_budget import get_uma_snapshot

            uma = get_uma_snapshot()
            if uma.rss_gib >= 5.5 or uma.is_critical or uma.is_emergency:
                log.debug("[F259] Synthesis skipped -- UMA RSS=%.1fGB, state=%s", uma.rss_gib, uma.state)
                self._result.synthesis_success = False
                self._result.synthesis_engine = "uma_guard"
                return
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F259] UMA check failed: %s", e)
        if not self._result.accepted_findings:
            log.info("[F259-SYN] early-exit: no findings, skipping synthesis")
            return
        if duckdb_store is None:
            log.debug("[F259] Synthesis skipped -- no duckdb_store")
            return
        findings: list[dict] = []
        try:
            if hasattr(duckdb_store, "get_top_findings"):
                findings = await duckdb_store.get_top_findings(limit=15)
            elif hasattr(duckdb_store, "get_recent_findings"):
                findings = await duckdb_store.get_recent_findings(limit=15)
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            log.debug("[F259] Failed to get findings: %s", e)
            return
        if not findings:
            log.debug("[F259] Synthesis skipped -- no findings")
            return
        if self._result.hermes_load_reason == "deferred":
            log.debug("[Phase4] Loading Hermes on-demand (findings=%d)", len(findings))
            try:
                await self._load_hermes_for_sprint()
            except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.warning("[Phase4] Hermes load failed, continuing without: %s", e)
                self._result.hermes_model_loaded = False
        try:
            from hledac.universal.brain.model_lifecycle import ModelLifecycle
            from hledac.universal.brain.synthesis_runner import SynthesisRunner
        except ImportError as e:
            log.debug("[F259] SynthesisRunner import failed: %s", e)
            self._result.synthesis_engine = "import_failed"
            return
        try:
            runner = SynthesisRunner(ModelLifecycle())
            runner.set_compression_threshold(4000)
            runner._duckdb_store = duckdb_store
            if lifecycle is not None:
                runner.inject_lifecycle_adapter(lifecycle)
            try:
                stix_graph = getattr(duckdb_store, "get_stix_graph", None)
                if stix_graph:
                    stix = stix_graph()
                    if stix is not None:
                        runner.inject_stix_graph(stix)
            except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                pass
            report = await runner.synthesize_findings(query=query, findings=findings, force_synthesis=True)
            self._result.synthesis_findings_count = len(findings)
            self._result.synthesis_success = report is not None
            self._result.synthesis_engine = (
                getattr(runner, "_last_synthesis_engine", "synthesis_runner") or "synthesis_runner"
            )
            if report is not None:
                try:
                    self._result.synthesis_text = _msgspec_encode(
                        {
                            "query": query,
                            "ioc_entities": [
                                {"type": e.ioc_type, "value": e.value}
                                for e in getattr(report, "ioc_entities", None) or []
                            ],
                            "threat_summary": getattr(report, "threat_summary", ""),
                            "threat_actors": list(getattr(report, "threat_actors", None) or []),
                            "confidence": getattr(report, "confidence", 0.0),
                            "sources_count": getattr(report, "sources_count", 0),
                            "timestamp": getattr(report, "timestamp", 0.0),
                        }
                    ).decode("utf-8")
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    self._result.synthesis_text = str(report)[:4096]
                log.info(
                    "[F259] Synthesis complete: success=%s, findings=%d",
                    self._result.synthesis_success,
                    self._result.synthesis_findings_count,
                )
                try:
                    hermes = getattr(runner, "_hermes_engine", None)
                    if hermes is not None:
                        batcher = getattr(hermes, "_mlx_batcher", None)
                        if batcher is not None and hasattr(batcher, "get_stats"):
                            self._result.mlx_batcher_stats = batcher.get_stats()
                            log.debug("[F285] batcher stats: %s", self._result.mlx_batcher_stats)
                except Exception as _e:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
                    log.debug("[F285] batcher stats collection failed: %s", _e)
            else:
                self._result.synthesis_text = ""
            await runner.close()
        except Exception as e:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
            log.debug("[F259] Synthesis failed: %s", e)
            self._result.synthesis_success = False
            self._result.synthesis_engine = "error"
            self._result.synthesis_text = ""

    async def _run_social_identity_surface_sidecar(self, query: str, duckdb_store: Any) -> None:
        """F204I: Social Identity Surface Miner — extract usernames/profiles from findings.

        Wire point: called in WINDUP phase after all acquisition lanes complete.
        Canonical execution path via SprintScheduler (not SidecarRegistry) to avoid
        double-execution — the SidecarRegistry adapter is wiring-only (returns []).

        Gates:
        - HLEDAC_ENABLE_SOCIAL_IDENTITY_SURFACE=1 (default: 0, opt-in)
        - duckdb_store is not None
        - self._result.accepted_findings is not empty

        Args:
            query: Original sprint query string
            duckdb_store: DuckDBShadowStore instance for canonical write
        """
        if duckdb_store is None:
            return
        if not getattr(self._result, "accepted_findings", None):
            return
        if not ENV.get_bool("HLEDAC_ENABLE_SOCIAL_IDENTITY_SURFACE"):
            log.debug("[F204I] Social Identity Surface skipped -- HLEDAC_ENABLE_SOCIAL_IDENTITY_SURFACE != '1'")
            return
        try:
            from hledac.universal.utils.uma_budget import get_uma_snapshot

            uma = get_uma_snapshot()
            if uma.rss_gib >= 5.5 or uma.is_critical or uma.is_emergency:
                log.debug("[F204I] UMA guard: RSS=%.1fGB, state=%s -- skipping", uma.rss_gib, uma.state)
                return
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        try:
            from hledac.universal.intelligence.social_identity_miner import create_social_identity_miner_adapter
        except Exception as e:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
            log.debug("[F204I] Import failed: %s", e)
            return
        try:
            miner = create_social_identity_miner_adapter()
        except Exception as e:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
            log.debug("[F204I] Factory failed: %s", e)
            return
        try:
            result = await miner.mine(findings=self._result.accepted_findings, store=duckdb_store, query=query)
            log.info(
                "[F204I] Social Identity scan complete: scanned=%d, facets=%d, elapsed_ms=%.1f",
                result.scanned_count,
                len(result.facets),
                result.elapsed_ms,
            )
        except Exception as e:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
            log.debug("[F204I] mine() failed: %s", e)

    async def _run_epistemic_gap_advisory(self, query: str, duckdb_store: Any) -> None:
        """
               Sprint F260: Run EpistemicGapProgram and ContradictionResolverProgram.

               Wire point: called after _run_synthesis_sidecar in WINDUP phase.

               Gates:
        - HLEDAC_ENABLE_LLM=1 (same as synthesis)
                 - RAM < 5.0GB (tighter than synthesis's 5.5GB)

               Part A: EpistemicGapProgram
                 - Inputs: findings from sprint + known gaps from ResearchSessionMemory
                 - Output: gaps written to ResearchSessionMemory via record_sprint_outcome()

               Part B: ContradictionResolverProgram
                 - Triggered when DS conflict_mass > 0.3
                 - Max 5 contradictions per call (M1 constraint)
        """
        if not ENV.get_bool("HLEDAC_ENABLE_LLM"):
            log.debug("[F260] Epistemic gap advisory skipped -- HLEDAC_ENABLE_LLM != '1'")
            return
        try:
            from hledac.universal.utils.uma_budget import get_uma_snapshot

            uma = get_uma_snapshot()
            if uma.rss_gib >= 5.0 or uma.is_critical or uma.is_emergency:
                log.debug("[F260] Epistemic gap skipped -- UMA RSS=%.1fGB, state=%s", uma.rss_gib, uma.state)
                return
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F260] UMA check failed: %s", e)
            return
        if duckdb_store is None:
            log.debug("[F260] Epistemic gap skipped -- no duckdb_store")
            return
        findings: list[dict] = []
        try:
            if hasattr(duckdb_store, "get_top_findings"):
                findings = await duckdb_store.get_top_findings(limit=30)
            elif hasattr(duckdb_store, "get_recent_findings"):
                findings = await duckdb_store.get_recent_findings(limit=30)
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            log.debug("[F260] Failed to get findings: %s", e)
            return
        if not findings:
            log.debug("[F260] Epistemic gap skipped -- no findings")
            return
        try:
            from hledac.universal.brain.dspy_programs import (
                MAX_EPISTEMIC_FINDINGS,
                ContradictionResolverProgram,
                EpistemicGapProgram,
            )
            from hledac.universal.brain.evidence_fusion import DempsterShafer
            from hledac.universal.knowledge.research_memory import ResearchSessionMemory
        except ImportError as e:
            log.debug("[F260] DSPy/DS import failed: %s", e)
            return
        try:
            memory = ResearchSessionMemory.get_instance()
            known_gaps: list[str] = []
            if memory is not None:
                try:
                    conn = memory._get_conn()
                    result = conn.execute("SELECT gaps_json FROM research_sessions ORDER BY ts DESC LIMIT 1").fetchone()
                    if result and result[0]:
                        gaps_data = _msgspec_decode(result[0]) if result[0] else []
                        known_gaps = [g.get("description", "") for g in gaps_data if g]
                except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
                    pass
            finding_strings = []
            for f in findings[:MAX_EPISTEMIC_FINDINGS]:
                text = f.get("payload_text", "") or f.get("title", "") or str(f)
                finding_strings.append(text[:200])
            gap_program = EpistemicGapProgram()
            pred = gap_program.forward(findings=finding_strings, known_gaps=known_gaps, query=query)
            identified_gaps = []
            if hasattr(pred, "gaps") and pred.gaps:
                if isinstance(pred.gaps, list):
                    identified_gaps = pred.gaps
                else:
                    identified_gaps = [str(pred.gaps)]
            log.info("[F260] Epistemic gaps identified: %d", len(identified_gaps))
            if memory is not None and identified_gaps:
                try:
                    await memory.record_sprint_outcome(
                        sprint_id=getattr(self._result, "sprint_id", "unknown"),
                        query=query,
                        findings=findings,
                        gaps=identified_gaps,
                    )
                    log.info("[F260] Gaps written to ResearchSessionMemory")
                except Exception as e:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                    log.debug("[F260] Failed to write gaps to memory: %s", e)
        except Exception as e:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            log.debug("[F260] EpistemicGapProgram failed: %s", e)
        try:
            contradictory_findings: list[dict] = []
            for f in findings[:20]:
                evidence = f.get("evidence", [])
                if evidence:
                    hypotheses = {e.get("hypothesis", "present") for e in evidence}
                    hypotheses.add("absent")
                    ds = DempsterShafer(hypotheses=hypotheses)
                    for e in evidence:
                        ds.add_evidence(
                            hypothesis=e.get("hypothesis", "present"),
                            mass=e.get("mass", 0.5),
                            source_weight=e.get("source_weight", 1.0),
                        )
                    conflict = ds.conflict_mass()
                    if conflict > 0.3:
                        contradictory_findings.append(
                            {
                                "finding": f.get("payload_text", "") or str(f)[:150],
                                "conflict_mass": conflict,
                                "source": f.get("source_type", "unknown"),
                            }
                        )
            if contradictory_findings:
                log.info("[F260] Found %d contradictory findings (DS conflict > 0.3)", len(contradictory_findings))
                resolver = ContradictionResolverProgram()
                pred = resolver.forward(contradictory_findings=contradictory_findings, context=f"Sprint query: {query}")
                log.info("[F260] Contradiction resolution complete")
                if hasattr(pred, "confidence") and pred.confidence:
                    log.info("[F260] Resolution confidence: %.2f", pred.confidence)
        except Exception as e:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            log.debug("[F260] ContradictionResolverProgram failed: %s", e)

    _background_research_tasks: set[asyncio.Task[None]] = set()

    def _maybe_launch_enhanced_research(self) -> None:
        """Fire-and-forget deep research advisory. Called at TEARDOWN."""
        if not self._config.deep_research_enabled:
            return
        try:
            from hledac.universal.utils.uma_budget import get_uma_snapshot

            snapshot = get_uma_snapshot()
            if snapshot.is_warn or snapshot.is_critical or snapshot.is_emergency:
                log.debug("[F11] Deep research blocked -- memory pressure: %s", snapshot.state)
                return
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        task = safe_create_task(self._run_enhanced_research_async())
        self._background_research_tasks.add(task)
        task.add_done_callback(self._background_research_tasks.discard)
        log.info("[F11] Deep research advisory launched (fire-and-forget)")
        try:
            dark_task = safe_create_task(self._run_dark_surface_pivot_advisory())
            self._background_research_tasks.add(dark_task)
            dark_task.add_done_callback(self._background_research_tasks.discard)
            log.debug("[F214K] Dark surface pivot advisory launched")
        except Exception:  # noqa: BLE001 — best-effort; callback handler; non-critical
            pass

    async def _run_enhanced_research_async(self) -> None:
        """Async wrapper -- runs deep research advisory with 180s timeout."""
        try:
            async with asyncio.timeout(180.0):
                await self._run_enhanced_research()
        except TimeoutError:
            log.warning("[F11] Deep research timed out after 180s")
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning(f"[F11] Deep research advisory failed: {e}")

    async def _run_enhanced_research(self) -> list:
        """

        F11: Run enhanced/deep research advisory post-sprint.



        GHOST_INVARIANTS: fail-soft, named except, CancelledError propagated.

        """
        try:
            from hledac.universal.enhanced_research import ResearchDepth, UnifiedResearchConfig, UnifiedResearchEngine
            from hledac.universal.security.deep_research_security import DeepResearchSecurity
        except ImportError:
            log.debug("[F11] enhanced_research not available")
            return []
        if not self._config.deep_research_enabled:
            return []
        if getattr(self._result, "accepted_findings", 0) < 3:
            return []
        try:
            from hledac.universal.utils.uma_budget import get_uma_snapshot

            snap = get_uma_snapshot()
            if snap.get("is_warn") or snap.get("is_critical") or snap.get("is_emergency"):
                level = snap.get("uma_pressure_level", "unknown")
                log.debug("[F11] Deep research blocked -- RAM pressure: %s", level)
                return []
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        seed_iocs: list[str] = []
        for src_field in (
            "pivot_seed_domains",
            "pivot_seed_ips",
            "pivot_seed_urls",
            "pivot_seed_hashes",
            "pivot_seed_cves",
            "next_seeds_ioc_domains",
            "next_seeds_ioc_ips",
            "next_seeds_ioc_urls",
            "next_seeds_ioc_hashes",
            "next_seeds_ioc_cves",
        ):
            for v in getattr(self._result, src_field, ()) or ():
                if isinstance(v, str) and v and (v not in seed_iocs):
                    seed_iocs.append(v)
                if len(seed_iocs) >= 10:
                    break
            if len(seed_iocs) >= 10:
                break
        if seed_iocs:
            query = " ".join(seed_iocs[:10])
        else:
            query = getattr(self._config, "sprint_query", "") or "OSINT"
        depth = ResearchDepth.EXHAUSTIVE if self._config.extreme_mode else ResearchDepth.ADVANCED
        try:
            start_mono = getattr(self, "_sprint_start_monotonic", None) or _time.monotonic()
            elapsed = _time.monotonic() - start_mono
        except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            elapsed = 0.0
        remaining_s = max(60.0, min(180.0, 1800.0 - elapsed))
        engine_config = UnifiedResearchConfig(
            depth=depth,
            max_concurrent_tools=2,
            enable_temporal_analysis=True,
            enable_data_leak_check=False,
            enable_archive_search=True,
            enable_stealth_crawling=False,
            cache_results=True,
            cache_ttl_seconds=3600,
        )
        _mode = "aggressive" if self._config.aggressive_mode else "research"
        _deep_sec = DeepResearchSecurity(_build_deep_security_config(_mode))
        try:
            async with _deep_sec.protected_session() as _sec_ctx:
                engine = UnifiedResearchEngine(config=engine_config)
                async with asyncio.timeout(remaining_s):
                    response = await engine.deep_research(query=query, depth=depth, max_results=50)
        except TimeoutError:
            log.warning(f"[F11] UnifiedResearchEngine timed out after {remaining_s:.0f}s")
            return []
        except Exception as e:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
            log.warning(f"[F11] UnifiedResearchEngine failed: {e}")
            return []
        if not response or not response.findings:
            return []
        canonicals = []
        for f in response.findings[:100]:
            try:
                import time as _time_module

                from hledac.universal.capabilities import CanonicalFinding

                ts = getattr(f, "timestamp", None)
                ts_float = ts.timestamp() if ts else _time_module.time()
                confidence = getattr(f, "relevance_score", 0.5) * getattr(f, "credibility_score", 0.5)
                src = getattr(f, "src", "enhanced_research")
                canonical = CanonicalFinding(
                    finding_id=f"er_{getattr(f, 'id', 'unknown')}",
                    query=f"deep_research:{getattr(self._result, 'sprint_id', 'unknown')}",
                    source_type=getattr(f, "source_type", src) or src,
                    confidence=confidence,
                    ts=ts_float,
                    provenance=getattr(f, "url", "") or "",
                    payload_text=f"{getattr(f, 'title', '')}\n{getattr(f, 'content', '')[:2000]}",
                )
                canonicals.append(canonical)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        if not canonicals:
            return []
        try:
            store = getattr(self, "_duckdb_store", None)
            if store and hasattr(store, "async_ingest_findings_batch"):
                _ = await self._gate_then_ingest_and_accumulate(store, canonicals, sprint_id=self.sprint_id or "")
                log.info(f"[F11] Deep research ingested {len(canonicals)} findings")
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            log.warning(f"[F11] DuckDB ingest failed: {e}")
        return canonicals

    async def _run_dark_surface_pivot_advisory(self) -> None:
        """

        F214K: Generate and enqueue dark surface pivot queries (onion/IPFS/DHT/I2P)

        post-sprint if accepted_findings >= 5 and HLEDAC_ENABLE_DARK_PIVOTS=1.



        GHOST_INVARIANTS: fail-soft, named except, transport availability verified

        before any query is enqueued.

        """
        try:
            if not ENV.get_bool("HLEDAC_ENABLE_DARK_PIVOTS"):
                return
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return
        try:
            accepted = getattr(self._result, "accepted_findings", 0) or 0
            if accepted < 5:
                return
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return
        tor_available = False
        i2p_available = False
        try:
            from hledac.universal.transport.tor_transport import TorTransport

            tor_available = TorTransport is not None
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        try:
            from hledac.universal.transport.i2p_transport import I2PTransport

            i2p_available = I2PTransport is not None
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        if not (tor_available or i2p_available):
            log.debug("[F214K] No dark transport available, skipping")
            return
        findings_for_dark: list = []
        try:
            store = getattr(self, "_duckdb_store", None)
            if store and hasattr(store, "_findings_cache"):
                findings_for_dark = list(getattr(store, "_findings_cache", [])[:50])
        except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            pass
        if not findings_for_dark:
            log.debug("[F214K] No findings available for dark surface queries")
            return
        dark_queries: list = []
        try:
            from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine

            hyp_eng = HypothesisEngine()
            dark_queries = await hyp_eng.generate_dark_surface_queries(
                findings=findings_for_dark,
                hermes_engine=self._hermes_engine if self._hermes_engine is not None else None,
                tor_available=tor_available,
                i2p_available=i2p_available,
            )
        except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F214K] Dark surface query generation failed: %s", _e)
            dark_queries = []
        _rc = None
        if dark_queries:
            try:
                from uuid import uuid4

                from brain.hypothesis_engine._types import DarkQuery
                from hledac.universal.research_context import ResearchContext, ResearchContextStatus

                _hyps = []
                for _dq in dark_queries:
                    if isinstance(_dq, DarkQuery):
                        _hyps.append(
                            type(
                                "Hypothesis",
                                (),
                                {
                                    "hypothesis_id": str(uuid4()),
                                    "statement": f"Dark query: {_dq.query} (type={_dq.query_type.value}, priority={_dq.priority:.2f})",
                                    "status": ResearchContextStatus.PENDING,
                                    "confidence": max(0.3, min(0.9, _dq.priority)),
                                    "supporting_evidence": list(_dq.source_iocs) if _dq.source_iocs else [],
                                    "contradicting_evidence": [],
                                },
                            )()
                        )
                from hledac.universal.research_context import ResearchContext

                _rc = ResearchContext(
                    query=self._query or "dark_surface_pivot",
                    research_id=getattr(self, "sprint_id", "") or str(uuid4()),
                )
                _rc.attach_hypothesis_engine(hyp_eng)
                for _h in _hyps:
                    _rc.add_hypothesis(_h)
                self._result.research_context = _rc
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        if not dark_queries:
            log.debug("[F214K] No dark surface queries generated")
            return
        planned = 0
        try:
            from hledac.universal.pipeline.pivot_lane_planner import _plan_dark_surface_pivot

            items: list = []
            seen_pairs: set = set()
            _plan_dark_surface_pivot(
                dark_queries, items, seen_pairs, tor_available=tor_available, i2p_available=i2p_available
            )
            planned = len(items)
            log.info(f"[F214K] Planned {planned} dark surface pivot queries")
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning(f"[F214K] Dark pivot planning failed: {e}")
        self._result.dark_surface_pivots_attempted = len(dark_queries)
        self._result.dark_surface_pivots_accepted = planned

    async def _close_dedup(self) -> None:
        """Close LMDB at TEARDOWN. Calls flush first."""
        if self._dedup_loading_task is not None and (not self._dedup_loading_task.done()):
            self._dedup_loading_task.cancel()
            try:
                await self._dedup_loading_task
            except asyncio.CancelledError:
                pass
            self._dedup_loading_task = None
        await self._flush_dedup()
        if self._dedup_env is not None:
            try:
                self._dedup_env.close()
            except Exception as exc:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                log.warning(f"Dedup LMDB close failed: {exc}")
            self._dedup_env = None
        if self._duckdb_read_con is not None:
            try:
                self._duckdb_read_con.close()
            except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                pass
            self._duckdb_read_con = None

    async def _init_forensics(self) -> None:
        """Initialize forensics enricher and LMDB. Fail-safe -- does not raise."""
        try:
            from forensics.enrichment_service import ForensicsEnricher

            self._forensics_enricher = ForensicsEnricher()
            await self._forensics_enricher.initialize()
        except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("Forensics enricher init failed: %s", exc)
            self._forensics_enricher = None
        try:
            db_path = _get_forensics_lmdb_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard

            self._forensics_lmdb_env = open_lmdb_with_guard(db_path, map_size=50 * 1024 * 1024, max_dbs=1)
        except Exception as exc:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            log.debug("Forensics LMDB open failed: %s", exc)
            self._forensics_lmdb_env = None

    async def _flush_forensics(self) -> None:
        """Flush forensics LMDB. Called at WINDUP. No-op if not initialized."""
        pass

    async def _close_forensics(self) -> None:
        """Close forensics enricher and LMDB at TEARDOWN."""
        if self._forensics_enricher is not None:
            try:
                await self._forensics_enricher.close()
            except Exception as exc:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                log.debug("Forensics enricher close failed: %s", exc)
            self._forensics_enricher = None
        if self._forensics_lmdb_env is not None:
            try:
                self._forensics_lmdb_env.close()
            except Exception as exc:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                log.debug("Forensics LMDB close failed: %s", exc)
            self._forensics_lmdb_env = None

    async def _init_multimodal(self) -> None:
        """Initialize multimodal enricher and LMDB. Fail-safe -- does not raise."""
        try:
            from multimodal.analyzer import MultimodalEnricher

            self._multimodal_enricher = MultimodalEnricher(governor=self._governor, embedding_dim=1280, batch_size=4)
            await self._multimodal_enricher.initialize()
        except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("Multimodal enricher init failed: %s", exc)
            self._multimodal_enricher = None
        try:
            db_path = _get_multimodal_lmdb_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard

            self._multimodal_lmdb_env = open_lmdb_with_guard(db_path, map_size=50 * 1024 * 1024, max_dbs=1)
        except Exception as exc:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            log.debug("Multimodal LMDB open failed: %s", exc)
            self._multimodal_lmdb_env = None

    async def _flush_multimodal(self) -> None:
        """Flush multimodal LMDB. Called at WINDUP. No-op if not initialized."""
        pass

    async def _close_multimodal(self) -> None:
        """Close multimodal enricher and LMDB at TEARDOWN."""
        if self._multimodal_enricher is not None:
            try:
                await self._multimodal_enricher.close()
            except Exception as exc:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                log.debug("Multimodal enricher close failed: %s", exc)
            self._multimodal_enricher = None
        if self._multimodal_lmdb_env is not None:
            try:
                self._multimodal_lmdb_env.close()
            except Exception as exc:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                log.debug("Multimodal LMDB close failed: %s", exc)
            self._multimodal_lmdb_env = None

    async def _init_metrics_registry(self) -> None:
        """

        Initialize MetricsRegistry fail-soft using config export_dir or default path.



        No absolute paths outside paths.py. Run dir is derived from export_dir

        (if set) or ~/.hledac/runs (default fallback). Metrics file lives under

        run_dir/logs/metrics.jsonl.

        """
        try:
            from hledac.universal.metrics_registry import MetricsRegistry

            export_dir = self._config.export_dir
            if export_dir:
                run_dir = Path(export_dir)
            else:
                run_dir = Path.home() / ".hledac" / "runs"
            run_dir.mkdir(parents=True, exist_ok=True)
            correlation = {
                "run_id": self.sprint_id or "default",
                "branch_id": None,
                "provider_id": None,
                "action_id": None,
            }
            self._metrics_registry = MetricsRegistry(
                run_dir=run_dir, run_id=self.sprint_id or "default", correlation=correlation
            )
            self._metrics_initialized = True
            log.debug(f"[F205H] MetricsRegistry initialized: run_dir={run_dir}")
        except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            self._metrics_registry = None
            self._metrics_initialized = False
            log.debug(f"[F205H] MetricsRegistry init failed (non-fatal): {exc}")

    def _tick_metrics_on_cycle_end(self) -> None:
        """

        Tick metrics at cycle completion -- captures RSS, open FDs.



        Called once per cycle (not in tight loop). Fail-soft: noop if registry

        not initialized. No model load, no model inference.

        """
        if not self._metrics_initialized or self._metrics_registry is None:
            return
        try:
            self._metrics_registry.tick()
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass

    def _get_metrics_summary(self) -> dict | None:
        """

        Get metrics summary for sprint report embedding.



        Returns lightweight state snapshot: counters/gauges count,

        last_rss_mb, persist_available. Fail-soft: returns None if registry

        not initialized.

        """
        if not self._metrics_initialized or self._metrics_registry is None:
            return None
        try:
            summary = self._metrics_registry.get_summary()
            return {
                "counter_count": summary.get("counter_count", 0),
                "gauge_count": summary.get("gauge_count", 0),
                "last_rss_mb": summary.get("gauges", {}).get("memory_rss_mb", 0.0),
                "persist_available": summary.get("persist_available", False),
                "closed": summary.get("closed", False),
            }
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            return None

    async def _close_metrics_registry(self) -> None:
        """

        Close metrics registry at TEARDOWN -- force flush prevents tail-loss.



        CancelledError is re-raised per GHOST_INVARIANTS.

        """
        if self._metrics_registry is None:
            return
        try:
            self._metrics_registry.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
            log.debug(f"[F205H] MetricsRegistry close failed: {exc}")
        finally:
            self._metrics_registry = None

    async def _prewarm_hermes_for_sprint(self) -> None:
        """

        P12: Mode-aware Hermes prewarm policy.



        Aggressive mode: prewarm blocks until Hermes is loaded, unless RSS > 4GB

        (hard headroom rule -- skip fail-soft, ToT is skipped for that run).



        Stable mode: current safe behavior via ModelManager memory guards

        (soft pressure clear + hard admission gate -- no RSS 4GB pre-check).



        Bounded lifecycle: loaded once at BOOT/WARMUP, released at TEARDOWN.

        Fail-soft: memory pressure on load skips ToT, does not abort sprint.



        F203J: Quantization budget respected via QuantizationSelector advisory

        in ModelManager._load_model_async. Budget is logged here for visibility.

        """
        RSS_PREWARM_HEADROOM_GB = 4.0
        if self._config.aggressive_mode:
            from hledac.universal.brain.model_manager import _get_current_rss_gb

            rss_before = _get_current_rss_gb()
            if rss_before > RSS_PREWARM_HEADROOM_GB:
                log.debug(
                    f"[P12] Skipping Hermes prewarm -- RSS {rss_before:.2f}GB > {RSS_PREWARM_HEADROOM_GB}GB headroom threshold"
                )
                self._hermes_engine = None
                self._memory_manager = None
                return
        hermes_synthesis_enabled = ENV.get_bool("HLEDAC_ENABLE_HERMES_SYNTHESIS")
        _hermes_force = bool(getattr(self, "_flags", None) is not None and getattr(self._flags, "hermes_force", False))
        if _hermes_force and (not hermes_synthesis_enabled):
            log.debug(
                "[F273D] Hermes force-load requested via flags.hermes_force -- overriding HLEDAC_ENABLE_HERMES_SYNTHESIS gate"
            )
            hermes_synthesis_enabled = True
        hermes_load_skipped_reason = None
        self._result.hermes_load_attempted = True
        if not hermes_synthesis_enabled:
            hermes_load_skipped_reason = "disabled_env"
            self._hermes_engine = None
            self._memory_manager = None
            self._result.hermes_load_reason = hermes_load_skipped_reason
            self._result.hermes_model_loaded = False
            log.debug(
                f"[F206AE] Hermes load skipped -- HLEDAC_ENABLE_HERMES_SYNTHESIS != '1', reason={hermes_load_skipped_reason}"
            )
        else:
            self._result.hermes_load_reason = "deferred"
            self._result.hermes_model_loaded = False
            log.debug("[Phase4] Hermes load deferred to synthesis sidecar.")

    async def _load_hermes_for_sprint(self) -> None:
        """
        P12: Load Hermes engine at sprint start via ModelManager.
        Bounded lifecycle: loaded at BOOT/WARMUP, released at TEARDOWN.
        Fail-soft: memory pressure on load skips ToT, does not abort sprint.

        M1 8GB invariant: ModelManager enforces bounded admission and RSS guards.

        F267: MLX prewarm -- if prewarm active and inter-sprint gap < 60s,
        model is still in Metal cache. Skip reload and verify.

        ISSUE-121: Serial model loading replaced with parallel prewarm via
        asyncio.to_thread() + asyncio.TaskGroup. Hermes load (~5-10s I/O-bound)
        now runs in background thread while ModernBERT + URL prefetch also run
        in parallel. Expected 4-7s → 1-2s (3-5× speedup).
        """

        async def _load_in_thread() -> None:
            """I/O-bound Hermes model load -- runs in thread pool, not event loop."""
            try:
                from hledac.universal.brain.deephermes3_engine import _MLX_PREWARM_ENABLED
            except ImportError:
                _MLX_PREWARM_ENABLED = False
            if _MLX_PREWARM_ENABLED:
                try:
                    from hledac.universal.brain.deephermes3_engine import (
                        _MLX_PREWARM_LAST_UNLOAD_TIME,
                        _MLX_PREWARM_SKIP_THRESHOLD_S,
                    )

                    if _MLX_PREWARM_LAST_UNLOAD_TIME is not None:
                        import time as _t_f267

                        gap = _t_f267.monotonic() - _MLX_PREWARM_LAST_UNLOAD_TIME
                        if gap < _MLX_PREWARM_SKIP_THRESHOLD_S:
                            log.debug(
                                f"[F267] MLX prewarm: last unload {gap:.1f}s ago < {_MLX_PREWARM_SKIP_THRESHOLD_S}s threshold -- verifying Metal cache"
                            )
                            from brain.deephermes3_engine import _verify_metal_cache_warm

                            warm = _verify_metal_cache_warm()
                            if warm:
                                import brain.deephermes3_engine as _dhe_mod

                                _dhe_mod._mlx_prewarm_active = True
                                self._result.hermes_load_reason = "prewarm_skip"
                                self._result.hermes_model_loaded = True
                                self._result.hermes_load_elapsed_s = 0.0
                                log.debug("[F267] MLX prewarm: model verified warm, skipping load")
                                return
                            log.debug("[F267] MLX prewarm: Metal cache cold, proceeding with load")
                except Exception as _e:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
                    log.debug("[F267] MLX prewarm check failed: %s", _e)
            import time as _t_f273d

            from hledac.universal.brain.model_manager import get_model_manager

            _hermes_t0 = _t_f273d.monotonic()
            try:
                # ISSUE-121: Use asyncio.to_thread() — runs blocking I/O in thread pool
                # WITHOUT creating nested event loop (F196C anti-pattern fix)
                self._hermes_engine = await asyncio.to_thread(get_model_manager().load_model, "hermes")
            except RuntimeError as e:
                log.debug(f"[P12] Skipping Hermes load -- ModelManager blocked: {e}")
                self._hermes_engine = None
                self._result.hermes_load_reason = f"rss_headroom_skip:{type(e).__name__}"
            except Exception as e:  # noqa: BLE001 — best-effort; lock acquisition failure; non-critical
                log.debug(f"[P12] Hermes load failed: {e}")
                self._hermes_engine = None
                self._result.hermes_load_reason = f"load_error:{type(e).__name__}"
            finally:
                self._result.hermes_load_elapsed_s = round(_t_f273d.monotonic() - _hermes_t0, 4)
                self._result.hermes_model_loaded = self._hermes_engine is not None
                self._memory_manager = None

        async def _prefetch_urls() -> None:
            """ISSUE-121: DNS prefetch during Hermes load — uses unified_transport.prefetch_dns.

            Replaces blocking socket.getaddrinfo (M1 thread pool contention)
            with async_getaddrinfo via prefetch_dns (LRU-bounded, fire-and-forget).
            """
            try:
                _query = getattr(self, "_query", "") or ""
                if _query and _query.startswith(("http://", "https://")):
                    # ISSUE-010: Delegate to unified_transport.prefetch_dns
                    # — async_getaddrinfo (no thread pool), LRU bounded (256 hosts),
                    #   60s TTL, fire-and-forget
                    from hledac.universal.transport import prefetch_dns
                    await prefetch_dns([_query])
            except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                pass

        async def _prefetch_modernbert() -> None:
            """ISSUE-121: Parallel ModernBERT warmup during Hermes load."""
            try:
                from hledac.universal.brain.modernbert_engine import ModernBertEngine

                engine = ModernBertEngine()
                if hasattr(engine, "_is_warm") and engine._is_warm:
                    return
                await asyncio.sleep(0)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_load_in_thread(), name="hermes_load", eager_start=True)
                tg.create_task(_prefetch_urls(), name="url_prefetch", eager_start=True)
                tg.create_task(_prefetch_modernbert(), name="modernbert_prefetch", eager_start=True)
        except* Exception as e:
            log.debug("[ISSUE-121] TaskGroup exception during parallel prewarm: %s", e)

    async def _unload_hermes_at_teardown(self) -> None:
        """
        P12: Unload Hermes engine at sprint teardown via ModelManager.

        Bounded lifecycle: loaded at BOOT/WARMUP, released at TEARDOWN.
        Uses ModelManager as canonical unload authority.

        F273H: Idle-based lazy unload — skip unload if Hermes was recently
        used (within _idle_unload_timeout_s window). Keeps model warm for
        next sprint when inter-sprint gap < 30 min.
        """
        from hledac.universal.brain.model_manager import get_model_manager

        if self._hermes_engine is None:
            return
        if hasattr(self._hermes_engine, "is_idle") and callable(self._hermes_engine.is_idle):
            if not self._hermes_engine.is_idle():
                log.debug("[P12][F273H] Hermes still active (idle check), skipping unload")
                return
            log.debug("[P12][F273H] Hermes idle timeout reached, unloading...")
        try:
            await get_model_manager().release_model("hermes")
            log.debug("[P12] Hermes unloaded via ModelManager")
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug(f"[P12] Hermes unload failed: {e}")
        finally:
            self._hermes_engine = None

    def is_duplicate(self, source_type: str, url: str, title: str = "") -> bool:
        """Check if (source_type, url, title) was already seen in any sprint.

        F1.1: Uses Rust BloomFilter for O(1) negative pre-check. On positive
        (might-be-seen), falls back to LMDB-backed set check.
        """
        if self._dedup_env is None:
            return False
        key = xxhash.xxh3_64(f"{source_type}:{url}:{title}".encode()).hexdigest()
        if self._dedup_rust is not None:
            try:
                if key not in self._dedup_rust:
                    return False
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        return key in self._dedup_seen

    def mark_seen(self, source_type: str, url: str, title: str = "", sprint_id: str = "") -> None:
        """Mark a finding as seen. Flush happens at WINDUP.

        F1.1: Inserts into Rust BloomFilter (fast negative pre-check) and
        Python set (exact LMDB-backed check).
        """
        if self._dedup_env is None:
            return
        key = xxhash.xxh3_64(f"{source_type}:{url}:{title}".encode()).hexdigest()
        self._dedup_seen.add(key)
        self._dedup_dirty = True
        if self._dedup_rust is not None:
            try:
                self._dedup_rust.add(key)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass

    def request_early_windup(self) -> None:
        """Sprint 8RA: Request early wind-down (called from UMA CRITICAL callback)."""
        if hasattr(self, "_lifecycle") and self._lifecycle is not None:
            self._lifecycle.request_windup()
        else:
            self._stop_requested = True

    def request_immediate_abort(self) -> None:
        """Sprint 8RA: Request immediate abort (called from UMA EMERGENCY callback)."""
        self._stop_requested = True
        self._result.aborted = True
        self._result.abort_reason = "uma_emergency"
        if hasattr(self, "_lifecycle") and self._lifecycle is not None:
            self._lifecycle.request_abort("uma_emergency")

    def is_new_entry(self, entry_hash: str) -> bool:
        """Return True if entry_hash has not been seen in this sprint.

        Uses LRU promotion: on hit, entry is moved to most-recently-used
        position so it survives longer under eviction pressure.
        """
        if not entry_hash:
            return True
        # ISSUE-3: get() does NOT promote; promote() is needed for LRU semantics.
        # This avoids unnecessary move_to_end on duplicate-check hot path.
        existing = self._seen_hashes.get(entry_hash)
        if existing is not None:
            self._result.duplicate_entry_hashes_skipped += 1
            return False
        self._seen_hashes[entry_hash] = True
        self._result.unique_entry_hashes_seen += 1
        return True

    def _get_prewindup_barrier_report(self) -> dict | None:
        """

        Sprint F207Q-A: Read prewindup barrier telemetry for diagnostic report.



        Returns dict under acquisition_strategy.prewindup_barrier key.

        Fails soft: returns None if barrier was never checked.

        """
        if not getattr(self._result, "prewindup_barrier_checked", False):
            return None
        return {
            "required_lanes": list(getattr(self._result, "prewindup_barrier_required_lanes", ())),
            "satisfied": getattr(self._result, "prewindup_barrier_satisfied", False),
            "attempted_lanes": list(getattr(self._result, "prewindup_barrier_attempted_lanes", ())),
            "skipped_lanes": dict(getattr(self._result, "prewindup_barrier_skipped_lanes", {})),
            "errors": dict(getattr(self._result, "prewindup_barrier_errors", {})),
            "duration_s": round(getattr(self._result, "prewindup_barrier_duration_s", 0.0), 3),
            "windup_delayed": getattr(self._result, "windup_delayed_for_nonfeed", False),
        }

    def _final_phase(self, lifecycle) -> None:
        """Mark teardown on lifecycle."""
        if hasattr(self, "_runner") and self._runner is not None:
            self._runner.teardown()
        else:
            self._final_phase_fallback(lifecycle)

    def _final_phase_fallback(self, lifecycle) -> None:
        """Fallback for direct calls to _final_phase (e.g. tests)."""
        try:
            from hledac.universal.runtime.sprint_lifecycle import SprintPhase

            phase = lifecycle.current_phase
            if phase == SprintPhase.WINDUP:
                lifecycle.mark_export_started()
                lifecycle.mark_teardown_started()
            elif phase not in (SprintPhase.EXPORT, SprintPhase.TEARDOWN):
                lifecycle.request_abort("scheduler_final_phase")
                lifecycle.mark_teardown_started()
        except Exception:  # noqa: BLE001 — best-effort; export/write failure; non-critical
            pass

    async def _maybe_export_partial(self, lifecycle) -> None:
        """

        Write a partial JSON artifact if the findings interval has been reached.



        Called every cycle in aggressive mode.  Also callable on early windup

        or abort to ensure the latest partial survives.

        """
        if not self._config.aggressive_mode:
            return
        interval = self._config.partial_export_findings_interval
        if interval <= 0:
            return
        delta = self._finding_count - self._last_partial_finding_count
        if delta < interval:
            return
        try:
            from hledac.universal.export.sprint_exporter import export_partial_sprint

            runtime_truth = {
                "is_meaningful": self._finding_count > 0,
                "accepted_findings": self._finding_count,
                "cycles_completed": self._result.cycles_completed,
                "cycles_started": self._result.cycles_started,
                "aggressive_mode": True,
            }
            scorecard = {
                "cycles_started": self._result.cycles_started,
                "cycles_completed": self._result.cycles_completed,
                "total_pattern_hits": self._result.total_pattern_hits,
            }
            handoff_dict = {
                "sprint_id": self.sprint_id or "unknown",
                "runtime_truth": runtime_truth,
                "scorecard": scorecard,
            }
            await export_partial_sprint(
                store=self._duckdb_store,
                handoff=handoff_dict,
                sprint_id=self.sprint_id or "unknown",
                finding_count=self._finding_count,
            )
            self._last_partial_finding_count = self._finding_count
            log.debug(f"[PARTIAL-EXPORT] triggered at finding_count={self._finding_count}")
        except Exception as ex:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning(f"[PARTIAL-EXPORT] _maybe_export_partial failed (non-fatal): {ex}")

    async def _run_export(self, lifecycle) -> None:
        """Run all four exporters; failure is fail-soft."""
        rend_md, rend_jsonld, rend_stix, rend_cti_stix, collect_cti_inputs = _import_exporters()
        report = await self._build_diagnostic_report(lifecycle)
        export_dir = self._config.export_dir

        def _render_sync(_render_fn: Any, _suffix: str) -> tuple[str, str | None]:
            try:
                _path = _render_fn(report, export_dir or None)
                return (_suffix, str(_path))
            except Exception as _exc:  # noqa: BLE001 — best-effort; export/write failure; non-critical
                return (_suffix, f"EXPORT_ERROR:{_suffix}:{_exc}")

        _render_tasks = [
            safe_create_task(asyncio.to_thread(_render_sync, _fn, _suffix), name=f"export:render:{_suffix}")
            for _fn, _suffix in [(rend_md, "md"), (rend_jsonld, "jsonld"), (rend_stix, "stix.json")]
        ]
        _async_tasks = [
            safe_create_task(
                self._run_cti_export(rend_cti_stix, collect_cti_inputs, report, export_dir), name="export:cti"
            ),
            safe_create_task(self._run_hypothesis_export(report, export_dir), name="export:hypothesis"),
        ]
        _all_tasks = _render_tasks + _async_tasks
        _results = await safe_gather_ok(*_all_tasks, label="export:all")
        for _result_item in _results:
            if isinstance(_result_item, tuple):
                _suffix, _path_or_err = _result_item
                self._result.export_paths.append(_path_or_err)

    async def _run_hypothesis_export(self, report: dict[str, Any], export_dir: str | None) -> None:
        """
        Sprint F259: Run causal hypothesis generation and export.

        Gate: HLEDAC_ENABLE_HYPOTHESIS=1 and RAM < 70%
        Runs after CTI STIX export in the post-export phase.

        GHOST_INVARIANTS:
        - fail-soft: export error must not prevent teardown
        - Lazy imports for causal_engine and hypothesis_graph
        - RAM check before execution
        """
        from export.hypothesis_builder import HYPOTHESIS_ENABLED, run_hypothesis_if_enabled

        if not HYPOTHESIS_ENABLED:
            return
        try:
            findings = report.get("findings", [])
            if not findings:
                logger.debug("Hypothesis export: no findings to process")
                return
            sprint_id = getattr(self._result, "sprint_id", "unknown")
            result = await run_hypothesis_if_enabled(findings=findings, sprint_id=sprint_id, output_dir=export_dir)
            if result.stix_bundle_path:
                self._result.export_paths.append(result.stix_bundle_path)
            self._result.hypotheses_generated = result.hypotheses_generated
            logger.info(
                f"Hypothesis export: {result.hypotheses_generated} hypotheses, {result.hidden_bridges} hidden bridges, {result.anomalies_detected} anomalies in {result.execution_time_s:.2f}s"
            )
        except Exception as exc:  # noqa: BLE001 — best-effort; logging failure; non-critical
            logger.warning(f"Hypothesis export failed (non-critical): {exc}")
            self._result.export_paths.append(f"EXPORT_ERROR:hypothesis:{exc}")

    async def _run_cti_export(
        self, render_cti_stix_to_path: Any, collect_cti_inputs: Any, report: dict[str, Any], export_dir: str | None
    ) -> None:
        """

        Sprint F204F: Run CTI STIX export with fail-soft error handling.



        GHOST_INVARIANTS:

        - asyncio.gather with return_exceptions=True

        - _check_gathered() after gather

        - asyncio.CancelledError re-raise

        - Large serialization (>1000 objects) via run_in_executor

        - RAM guard: MAX_STIX_OBJECTS=500

        - Fail-soft: EXPORT_ERROR logged, not raised

        """
        try:
            cti_inputs = await collect_cti_inputs(report, self._duckdb_store)
        except Exception as exc:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            self._result.export_paths.append(f"EXPORT_ERROR:cti_stix:{exc}")
            return
        try:
            if len(cti_inputs.findings) > 1000:
                path = await asyncio.to_thread(
                    render_cti_stix_to_path,
                    findings=list(cti_inputs.findings),
                    identity_candidates=list(cti_inputs.identity_candidates),
                    attribution_scores=cti_inputs.attribution_scores,
                    killchain_tags=cti_inputs.killchain_tags,
                    evidence_chains=list(cti_inputs.evidence_chains),
                    path=export_dir,
                )
            else:
                path = render_cti_stix_to_path(
                    findings=list(cti_inputs.findings),
                    identity_candidates=list(cti_inputs.identity_candidates),
                    attribution_scores=cti_inputs.attribution_scores,
                    killchain_tags=cti_inputs.killchain_tags,
                    evidence_chains=list(cti_inputs.evidence_chains),
                    path=export_dir,
                )
            self._result.export_paths.append(str(path))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — best-effort; export/write failure; non-critical
            self._result.export_paths.append(f"EXPORT_ERROR:cti_stix:{exc}")

    def _build_public_stage_counters(self) -> dict:
        """

        F208G-A: Build public_stage_counters dict from _public_pipeline_result.



        This aggregates all F208G-A public_* telemetry fields from the stored

        PipelineRunResult into a single dict for propagation to acquisition_report

        and source_family_outcomes.



        Returns an empty dict if _public_pipeline_result is None (PUBLIC skipped).

        """
        if self._public_pipeline_result is None:
            return {}
        pr = self._public_pipeline_result
        return {
            "public_discovered": getattr(pr, "public_discovered", 0) or 0,
            "public_fetch_candidate_count": getattr(pr, "public_fetch_candidate_count", 0) or 0,
            "public_fetch_attempted": getattr(pr, "public_fetch_attempted", 0) or 0,
            "public_fetch_success": getattr(pr, "public_fetch_success", 0) or 0,
            "public_fetch_failed": getattr(pr, "public_fetch_failed", 0) or 0,
            "public_acceptance_attempted": getattr(pr, "public_acceptance_attempted", 0) or 0,
            "public_acceptance_accepted": getattr(pr, "public_acceptance_accepted", 0) or 0,
            "public_acceptance_rejected": getattr(pr, "public_acceptance_rejected", 0) or 0,
            "public_acceptance_reject_reasons": getattr(pr, "public_acceptance_reject_reasons", {}) or {},
            "public_terminal_classified_count": getattr(pr, "public_terminal_classified_count", 0) or 0,
            "public_unclassified_count": getattr(pr, "public_unclassified_count", 0) or 0,
            "public_terminal_reason_counts": getattr(pr, "public_terminal_reason_counts", {}) or {},
            "public_skipped_duplicate": getattr(pr, "public_skipped_duplicate", 0) or 0,
            "public_skipped_unsupported_scheme": getattr(pr, "public_skipped_unsupported_scheme", 0) or 0,
            "public_skipped_memory_gate": getattr(pr, "public_skipped_memory_gate", 0) or 0,
            "public_skipped_quality_gate": getattr(pr, "public_skipped_quality_gate", 0) or 0,
            "security_rejected_count": getattr(pr, "security_rejected_count", 0) or 0,
            "pii_redacted_count": getattr(pr, "pii_redacted_count", 0) or 0,
            "public_skipped_browser_unavailable": getattr(pr, "public_skipped_browser_unavailable", 0) or 0,
            "public_skipped_xml_or_feed": getattr(pr, "public_skipped_xml_or_feed", 0) or 0,
            "public_skipped_timeout": getattr(pr, "public_skipped_timeout", 0) or 0,
            "public_skipped_fetch_error": getattr(pr, "public_skipped_fetch_error", 0) or 0,
            "public_skipped_url_sample": getattr(pr, "public_skipped_url_sample", ()) or (),
            "public_rejected_no_pattern_match": getattr(pr, "public_rejected_no_pattern_match", 0) or 0,
            "public_rejected_low_information": getattr(pr, "public_rejected_low_information", 0) or 0,
            "public_rejected_duplicate": getattr(pr, "public_rejected_duplicate", 0) or 0,
            "public_rejected_storage_rejected": getattr(pr, "public_rejected_storage_rejected", 0) or 0,
            "public_rejected_url_samples": getattr(pr, "public_rejected_url_samples", ()) or (),
            "public_accepted_url_sample": getattr(pr, "public_accepted_url_sample", ()) or (),
        }

    async def _build_diagnostic_report(self, lifecycle) -> dict:
        """Build a diagnostic report dict for exporters."""
        r = self._result
        _wg_call = getattr(r, "windup_guard_call_count", 0)
        _wg_supplied = getattr(r, "windup_guard_callback_supplied_count", 0)
        _wg_executed = getattr(r, "windup_guard_callback_executed_count", 0)
        _wg_last_reason = getattr(r, "windup_guard_last_reason", "") or ""
        _wg_last_phase = getattr(r, "windup_guard_last_phase", "") or ""
        _wg_last_allowed = getattr(r, "windup_guard_last_allowed", None)
        _wg_not_exec_reason = getattr(r, "windup_guard_last_callback_not_executed_reason", "") or ""
        _rg_checked = getattr(r, "return_guard_checked", False)
        _rg_satisfied = getattr(r, "return_guard_satisfied", False)
        _rg_delayed = getattr(r, "return_guard_delayed_for_nonfeed", False)
        _rg_block = getattr(r, "return_guard_block_reason", "") or ""
        _rg_attempted = list(getattr(r, "return_guard_attempted_lanes", ()) or ())
        _rg_skipped = dict(getattr(r, "return_guard_skipped_lanes", {}))
        _rg_errors = dict(getattr(r, "return_guard_errors", {}))
        _se_path = getattr(r, "scheduler_exit_path", None)
        _se_reason = getattr(r, "scheduler_exit_reason", None)
        _se_phase = getattr(r, "scheduler_exit_phase", None)
        _se_cycle = getattr(r, "scheduler_exit_cycle", None)
        _se_elapsed = getattr(r, "scheduler_exit_elapsed_s", None)
        _se_guard_checked = getattr(r, "scheduler_exit_guard_checked", False)
        _se_guard_required = list(getattr(r, "scheduler_exit_guard_required", ()) or ())
        _se_guard_satisfied = getattr(r, "scheduler_exit_guard_satisfied", None)
        _at_checked = getattr(r, "acquisition_terminality_checked", False)
        _at_satisfied = getattr(r, "acquisition_terminality_satisfied", False)
        _at_missing = list(getattr(r, "acquisition_terminality_missing_lanes", ()) or ())
        _at_report = getattr(r, "acquisition_terminality_report", {}) or {}
        _feed_active = getattr(r, "feed_budget_active", False)
        _feed_reason = getattr(r, "feed_budget_reason", "") or ""
        _feed_accepted_before = getattr(r, "feed_accepted_before_cap", 0)
        _feed_suppressed = getattr(r, "feed_suppressed_by_budget", 0)
        _feed_top = list(getattr(r, "top_feed_source_counts", ()) or ())
        _feed_max_per = getattr(r, "max_per_source_applied", "") or ""
        _exit_path = getattr(r, "scheduler_exit_path", None)
        run_id = self.sprint_id or f"8bk_sprint_{int(_time.time())}"
        report = {
            "run_id": run_id,
            "phase": lifecycle.current_phase.name,
            "cycles_started": r.cycles_started,
            "cycles_completed": r.cycles_completed,
            "unique_entry_hashes": r.unique_entry_hashes_seen,
            "duplicates_skipped": r.duplicate_entry_hashes_skipped,
            "pattern_hits": r.total_pattern_hits,
            "accepted_findings": r.accepted_findings,
            "aborted": r.aborted,
            "abort_reason": r.abort_reason,
            "stop_requested": r.stop_requested,
            "lifecycle_snapshot": lifecycle.snapshot(),
            "entries_per_source": dict(get_sprint_ctx().entries_per_source),
            "hits_per_source": dict(get_sprint_ctx().hits_per_source),
        }
        metrics_summary = self._get_metrics_summary()
        if metrics_summary:
            report["metrics_registry"] = metrics_summary
        try:
            from hledac.universal.knowledge.duckdb_store import get_arrow_metrics

            arrow_metrics = get_arrow_metrics()
            if arrow_metrics:
                report["arrow_ingest"] = arrow_metrics
        except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            pass
        graph_signal = self._get_graph_signal()
        if graph_signal:
            report["graph_signal"] = graph_signal
        shadow_preview = self._build_shadow_readiness_preview()
        if shadow_preview:
            report["shadow_pre_decision"] = shadow_preview
        intel = self.compute_sprint_intelligence()
        if intel.get("correlation"):
            report["correlation_summary"] = intel["correlation"]
        if intel.get("hypothesis_pack"):
            report["hypothesis_pack_summary"] = intel["hypothesis_pack"]
        if intel.get("branch_value"):
            report["branch_value"] = intel["branch_value"]
        windup_scorecard = self._get_windup_scorecard()
        if windup_scorecard:
            report["windup_scorecard"] = windup_scorecard
        source_health = self._get_source_health_summary()
        if source_health:
            report["source_health_summary"] = source_health
        circuit_state = self._get_circuit_breaker_summary()
        if circuit_state:
            report["circuit_breaker_state"] = circuit_state
        if self._acquisition_plan is not None:
            _lanes_list = [
                {
                    "lane": p.lane,
                    "enabled": p.enabled,
                    "reason": p.reason,
                    "max_items": p.max_items,
                    "timeout_s": p.timeout_s,
                    "concurrency": p.concurrency,
                    "risk_level": p.risk_level,
                }
                for p in self._acquisition_plan.plans
            ]
            _skipped = [p.lane for p in self._acquisition_plan.plans if not p.enabled]
            _executed = [p.lane for p in self._acquisition_plan.plans if p.enabled]
            report["acquisition_strategy"] = {
                "uma_state": self._acquisition_plan.uma_state,
                "swap_detected": self._acquisition_plan.swap_detected,
                "aggressive_mode": self._acquisition_plan.aggressive_mode,
                "stealth_ready": self._acquisition_plan.stealth_ready,
                "transport_degraded": self._acquisition_plan.transport_degraded,
                "enforced": True,
                "skipped_lanes": _skipped,
                "executed_lanes": _executed,
                "lanes": _lanes_list,
                "nonfeed_plan_debug": {
                    "domain_detected": self._acquisition_plan.nonfeed_plan_debug.domain_detected,
                    "wallet_detected": self._acquisition_plan.nonfeed_plan_debug.wallet_detected,
                    "enabled_nonfeed_lanes": list(self._acquisition_plan.nonfeed_plan_debug.enabled_nonfeed_lanes),
                    "disabled_nonfeed_lanes": list(self._acquisition_plan.nonfeed_plan_debug.disabled_nonfeed_lanes),
                    "disabled_reasons": list(self._acquisition_plan.nonfeed_plan_debug.disabled_reasons),
                    "scheduled_nonfeed_lanes": list(self._acquisition_plan.nonfeed_plan_debug.scheduled_nonfeed_lanes),
                    "hardware_skipped_lanes": list(self._acquisition_plan.nonfeed_plan_debug.hardware_skipped_lanes),
                    "nonfeed_execution_scheduled": self._acquisition_plan.nonfeed_plan_debug.nonfeed_execution_scheduled,
                    "nonfeed_execution_skip_reason": self._acquisition_plan.nonfeed_plan_debug.nonfeed_execution_skip_reason,
                }
                if self._acquisition_plan.nonfeed_plan_debug is not None
                else None,
                "prewindup_barrier": self._get_prewindup_barrier_report()
                if hasattr(self, "_get_prewindup_barrier_report")
                else None,
                "prewindup_barrier_checked": getattr(self._result, "prewindup_barrier_checked", False),
            }
        report["acquisition_terminality_checked"] = _at_checked
        report["acquisition_terminality_satisfied"] = _at_satisfied
        report["acquisition_terminality_missing_lanes"] = _at_missing
        report["windup_guard_observation"] = {
            "call_count": _wg_call,
            "callback_supplied_count": _wg_supplied,
            "callback_executed_count": _wg_executed,
            "last_reason": _wg_last_reason,
            "last_phase": _wg_last_phase,
            "last_allowed": _wg_last_allowed,
            "callback_not_executed_reason": _wg_not_exec_reason,
        }
        report["return_guard"] = {
            "checked": _rg_checked,
            "required_lanes": _rg_attempted,
            "satisfied": _rg_satisfied,
            "delayed_for_nonfeed": _rg_delayed,
            "block_reason": _rg_block,
            "attempted_lanes": _rg_attempted,
            "skipped_lanes": _rg_skipped,
            "errors": _rg_errors,
        }
        report["scheduler_exit"] = {
            "exit_path": _se_path,
            "exit_reason": _se_reason,
            "exit_phase": _se_phase,
            "exit_cycle": _se_cycle,
            "elapsed_s": _se_elapsed,
            "guard_checked": _se_guard_checked,
            "guard_required": _se_guard_required,
            "guard_satisfied": _se_guard_satisfied,
        }
        report["feed_dominance_budget"] = {
            "active": _feed_active,
            "reason": _feed_reason,
            "feed_accepted_before_cap": _feed_accepted_before,
            "feed_suppressed_by_budget": _feed_suppressed,
            "top_feed_source_counts": _feed_top,
            "max_per_source_applied": _feed_max_per,
        }
        if "acquisition_strategy" in report:
            report["acquisition_strategy"]["terminality"] = _at_report
            report["acquisition_strategy"]["acquisition_terminality_checked"] = _at_checked
            report["acquisition_strategy"]["acquisition_terminality_satisfied"] = _at_satisfied
            report["acquisition_strategy"]["acquisition_terminality_missing_lanes"] = _at_missing
        else:
            report["acquisition_strategy"] = {
                "terminality": _at_report,
                "acquisition_terminality_checked": _at_checked,
                "acquisition_terminality_satisfied": _at_satisfied,
                "acquisition_terminality_missing_lanes": _at_missing,
            }
        if self._lane_outcomes:
            _outcomes_list = [o.to_dict() if hasattr(o, "to_dict") else dict(o) for o in self._lane_outcomes]
            _planned = [p.lane for p in (self._acquisition_plan.plans if self._acquisition_plan else [])]
            _attempted = [o["lane"] for o in _outcomes_list if o.get("attempted")]
            _skipped_lanes = [
                lane
                for lane in _planned
                if lane not in _attempted and lane not in (_executed if self._acquisition_plan else [])
            ]
            _lane_errors = [o["error"] for o in _outcomes_list if o.get("error")]
            report["acquisition_lanes"] = {
                "planned": _planned,
                "attempted": _attempted,
                "skipped": _skipped_lanes,
                "outcomes": _outcomes_list,
                "total_optional_findings": sum((o.get("accepted_findings", 0) for o in _outcomes_list)),
                "lane_errors": _lane_errors,
            }
        _sfo: dict[str, dict] = {}
        for _fam, _lane in [
            ("ct", AcquisitionLane.CT),
            ("wayback", AcquisitionLane.WAYBACK),
            ("passive_dns", AcquisitionLane.PASSIVE_DNS),
            ("blockchain", AcquisitionLane.BLOCKCHAIN),
            ("feed", "FEED"),
            ("public", AcquisitionLane.PUBLIC),
            ("doh", AcquisitionLane.DOH),
        ]:
            _raw: dict | list | None = None
            if _lane == "FEED":
                _raw = getattr(self, "_feed_verdicts", []) or None
                if _raw is not None:
                    _feed_accepted = (
                        (self._result.accepted_findings or 0)
                        - (self._result.public_accepted_findings or 0)
                        - (self._result.ct_log_accepted_findings or 0)
                    )
                    _feed_accepted = max(0, _feed_accepted)
                    if isinstance(_raw, list) and len(_raw) > 0:
                        _first = _raw[0]
                        if isinstance(_first, tuple):
                            _raw = {
                                "verdict": _first,
                                "accepted_count": _feed_accepted,
                                "raw_count": _first[1] if len(_first) > 1 else 0,
                                "attempted": True,
                            }
            elif _lane == AcquisitionLane.PUBLIC:
                _raw = getattr(self, "_public_outcome", None)
            elif self._lane_outcomes:
                for _o in self._lane_outcomes:
                    if hasattr(_o, "lane") and _o.lane == _lane:
                        _raw = _o
                        break
            _sfo[_fam] = normalize_source_family_outcome(_fam, _raw)
        _sfo["academic"] = normalize_source_family_outcome("academic", None)
        if hasattr(self._result, "rdap_enrichment_attempted") and self._result.rdap_enrichment_attempted:
            _rdap_raw = {
                "attempted": True,
                "accepted_count": getattr(self._result, "rdap_enrichment_findings_stored", 0) or 0,
                "raw_count": getattr(self._result, "rdap_enrichment_findings_built", 0) or 0,
                "error": getattr(self._result, "rdap_enrichment_error", None),
                "terminal_state": "ATTEMPTED_ERROR"
                if getattr(self._result, "rdap_enrichment_error", None)
                else "ATTEMPTED_ACCEPTED"
                if getattr(self._result, "rdap_enrichment_findings_stored", 0)
                else "ATTEMPTED_NO_RESULTS",
            }
            _sfo["rdap_enrichment"] = normalize_source_family_outcome("rdap_enrichment", _rdap_raw)
        if hasattr(self._result, "rir_correlation_produced") and self._result.rir_correlation_produced > 0:
            _rir_raw = {
                "attempted": True,
                "accepted_count": self._result.rir_correlation_produced or 0,
                "raw_count": self._result.rir_correlation_produced or 0,
                "error": None,
                "terminal_state": "ATTEMPTED_ACCEPTED",
            }
            _sfo["rir_correlation"] = normalize_source_family_outcome("rir_correlation", _rir_raw)
        try:
            _term_uma = "ok"
            _term_swap = False
            if self._governor is not None:
                try:
                    _snap = await self._governor.evaluate()
                    _term_uma = getattr(_snap, "uma_state", "ok")
                    _term_swap = getattr(_snap, "swap_detected", False)
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
            _mlt = required_terminal_lanes(
                snapshot=self._acquisition_plan, query=self._query, uma_state=_term_uma, swap_detected=_term_swap
            )
            _canonical_outcomes = self._final_source_family_outcomes_for_terminality()
            _fresh_term = terminality_report(required_lanes=_mlt, observed_outcomes=_canonical_outcomes)
            if "acquisition_strategy" in report:
                report["acquisition_strategy"]["terminality"] = _fresh_term
                report["acquisition_strategy"]["acquisition_terminality_checked"] = True
                report["acquisition_strategy"]["acquisition_terminality_satisfied"] = (
                    len(_fresh_term.get("missing_lanes", [])) == 0
                )
                report["acquisition_strategy"]["acquisition_terminality_missing_lanes"] = list(
                    _fresh_term.get("missing_lanes", [])
                )
            report["acquisition_terminality_checked"] = True
            report["acquisition_terminality_satisfied"] = len(_fresh_term.get("missing_lanes", [])) == 0
            report["acquisition_terminality_missing_lanes"] = list(_fresh_term.get("missing_lanes", []))
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        report["public_terminal_state"] = _sfo.get("public", {}).get("terminal_state") or "NEVER_SCHEDULED"
        report["ct_terminal_state"] = _sfo.get("ct", {}).get("terminal_state") or "NEVER_ATTEMPTED"
        report["scheduler_exit_path"] = _exit_path
        report["return_guard_checked"] = _rg_checked
        _wg_irrelevant = frozenset({"not_applicable", "no_lanes_ran", "disabled", "skipped"})
        report["windup_guard_call_count"] = _wg_call
        report["windup_guard_reason"] = _wg_last_reason
        report["windup_guard_not_applicable"] = _wg_last_reason.lower() in _wg_irrelevant
        try:
            from hledac.universal.runtime.sprint_timer import compute_runtime_loop_telemetry

            _timer_ev = getattr(self._result, "timer_events", None) or []
            report["runtime_loop_telemetry"] = compute_runtime_loop_telemetry(_timer_ev)
        except Exception:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
            report["runtime_loop_telemetry"] = {
                "events": [],
                "phase_totals_s": {},
                "slowest_phases": [],
                "lane_timings": {},
                "timer_event_count": 0,
            }
        try:
            from hledac.universal.runtime.acquisition_strategy import build_acquisition_report

            _wg_obs = {
                "call_count": _wg_call,
                "callback_supplied_count": _wg_supplied,
                "callback_executed_count": _wg_executed,
                "last_reason": _wg_last_reason,
                "last_phase": _wg_last_phase,
                "last_allowed": _wg_last_allowed,
                "callback_not_executed_reason": _wg_not_exec_reason,
            }
            _rg_dict = {
                "checked": _rg_checked,
                "required_lanes": _rg_attempted,
                "satisfied": _rg_satisfied,
                "delayed_for_nonfeed": _rg_delayed,
                "block_reason": _rg_block,
                "attempted_lanes": _rg_attempted,
                "skipped_lanes": _rg_skipped,
                "errors": _rg_errors,
            }
            _pwb = None
            if hasattr(self, "_get_prewindup_barrier_report"):
                _pwb = self._get_prewindup_barrier_report()
                if _pwb is None and getattr(self._result, "prewindup_barrier_checked", False):
                    _pwb = {
                        "checked": True,
                        "satisfied": getattr(self._result, "prewindup_barrier_satisfied", True),
                        "required_lanes": (),
                        "attempted_lanes": (),
                        "skipped_lanes": {},
                        "errors": {},
                        "windup_delayed": getattr(self._result, "windup_delayed_for_nonfeed", False),
                        "nonfeed_scheduler_gap_resolved": getattr(self._result, "nonfeed_scheduler_gap_resolved", None),
                    }
                    logger.debug(
                        "[F214WINDUP] Synthetic prewindup_barrier injected: barrier checked=True but _get_prewindup_barrier_report() returned None (active300 early exit)"
                    )
            _se_dict = {
                "exit_path": _se_path,
                "exit_reason": _se_reason,
                "exit_phase": _se_phase,
                "exit_cycle": _se_cycle,
                "elapsed_s": _se_elapsed,
                "guard_checked": _se_guard_checked,
                "guard_required": _se_guard_required,
                "guard_satisfied": _se_guard_satisfied,
            }
            _nd = None
            if self._acquisition_plan is not None and self._acquisition_plan.nonfeed_plan_debug is not None:
                nd = self._acquisition_plan.nonfeed_plan_debug
                _nd = {
                    "domain_detected": nd.domain_detected,
                    "wallet_detected": nd.wallet_detected,
                    "enabled_nonfeed_lanes": list(nd.enabled_nonfeed_lanes),
                    "disabled_nonfeed_lanes": list(nd.disabled_nonfeed_lanes),
                    "disabled_reasons": list(nd.disabled_reasons),
                    "scheduled_nonfeed_lanes": list(nd.scheduled_nonfeed_lanes),
                    "hardware_skipped_lanes": list(nd.hardware_skipped_lanes),
                    "nonfeed_execution_scheduled": nd.nonfeed_execution_scheduled,
                    "nonfeed_execution_skip_reason": nd.nonfeed_execution_skip_reason,
                    "acquisition_profile": getattr(nd, "acquisition_profile", "default"),
                    "feed_cap_reason": getattr(nd, "feed_cap_reason", None),
                    "nonfeed_priority_enabled": getattr(nd, "nonfeed_priority_enabled", False),
                    "nonfeed_profile_expected_lanes": list(getattr(nd, "nonfeed_profile_expected_lanes", ()) or ()),
                    "pivot_executor_enabled": getattr(nd, "pivot_executor_enabled", False),
                    "pivot_candidates_count": getattr(nd, "pivot_candidates_count", 0),
                    "pivot_candidate_types": list(getattr(nd, "pivot_candidate_types", ()) or ()),
                    "pivot_scheduled_lanes": list(getattr(nd, "pivot_scheduled_lanes", ()) or ()),
                    "pivot_skip_reason": getattr(nd, "pivot_skip_reason", None),
                    "pivot_errors": list(getattr(nd, "pivot_errors", ()) or ()),
                    "mission_intent": getattr(nd, "mission_intent", "unknown"),
                    "mission_target_kind": getattr(nd, "mission_target_kind", "unknown"),
                    "mission_required_lanes": list(getattr(nd, "mission_required_lanes", ()) or ()),
                    "mission_optional_lanes": list(getattr(nd, "mission_optional_lanes", ()) or ()),
                    "mission_reason": getattr(nd, "mission_reason", ""),
                    "mission_runtime_applied": getattr(nd, "mission_runtime_applied", False),
                    "mission_lane_priority": list(getattr(nd, "mission_lane_priority", ()) or ()),
                    "mission_pivot_boost_applied": getattr(nd, "mission_pivot_boost_applied", False),
                    "mission_feed_cap_reason": getattr(nd, "mission_feed_cap_reason", None),
                    "feed_cap_applied_by_mission": getattr(nd, "feed_cap_applied_by_mission", False),
                    "feed_cap_mission_intent": getattr(nd, "feed_cap_mission_intent", None),
                }
            _sfo_list = list(_sfo.values())
            _sfo_by_family = {entry.get("family", ""): entry for entry in _sfo_list}
            _expected = (
                list(_nd.get("nonfeed_profile_expected_lanes", ()) or []) if _nd else nonfeed_expected_lanes or []
            )
            _FAM_MAP = {
                "CT": "ct",
                "WAYBACK": "wayback",
                "PASSIVE_DNS": "passive_dns",
                "BLOCKCHAIN": "blockchain",
                "PUBLIC": "public",
                "PIVOT_EXECUTOR": "pivot_executor",
                "DOH": "doh",
            }
            _surf_ct = _sfo_by_family.get("ct", {})
            _surf_public = _sfo_by_family.get("public", {})
            _surfaced_lower = {
                _FAM_MAP.get(fam.upper(), fam.lower())
                for fam, entry in _sfo_by_family.items()
                if entry.get("attempted") or entry.get("skipped")
            }
            _missing = [e for e in _expected if _FAM_MAP.get(e, e.lower()) not in _surfaced_lower]
            _elig = getattr(self._result, "nonfeed_lane_eligibility", {}) or {}
            for _lane_el in ("DOH", "WAYBACK", "PASSIVE_DNS"):
                _fam_key = _FAM_MAP.get(_lane_el, _lane_el.lower())
                _eligible = bool(_elig.get(_lane_el.lower()) or _elig.get(_lane_el, False))
                if _fam_key not in _surfaced_lower and _eligible and (_lane_el not in _missing):
                    _missing.append(_lane_el)
            _surf_wayback = _sfo_by_family.get("wayback", {})
            _surf_pdns = _sfo_by_family.get("passive_dns", {})
            if (not _surf_wayback or not _surf_wayback.get("terminal_state")) and "WAYBACK" in _missing:
                _surf_wayback = {"terminal_state": "not_scheduled"}
            if (not _surf_pdns or not _surf_pdns.get("terminal_state")) and "PASSIVE_DNS" in _missing:
                _surf_pdns = {"terminal_state": "not_scheduled"}
            _surf_complete = not _missing
            _pub_bootstrap_order = _surf_public.get("public_bootstrap_order", "disabled")
            _keyword_seed_fallback = _surf_public.get("keyword_seed_fallback_triggered", False)
            _pub_bootstrap_prevented = _surf_public.get("public_bootstrap_prevented_discovery_timeout", False)
            _pub_bootstrap_fetch_att = _surf_public.get("public_bootstrap_first_fetch_attempted", False)
            report["acquisition_report"] = build_acquisition_report(
                query=self._query,
                plan=self._acquisition_plan,
                terminality=_at_report,
                nonfeed_plan_debug=_nd,
                source_family_outcomes=_sfo_list,
                return_guard=_rg_dict,
                prewindup_barrier=_pwb,
                scheduler_exit=_se_dict,
                windup_guard_observation=_wg_obs,
                acquisition_profile=self._config.acquisition_profile
                if self._config.acquisition_profile is not None
                else "default",
                nonfeed_expected_lanes=_expected,
                nonfeed_missing_expected_lanes=_missing,
                wayback_terminal_state=_surf_wayback.get("terminal_state", ""),
                passive_dns_terminal_state=_surf_pdns.get("terminal_state", ""),
                nonfeed_surface_complete=_surf_complete,
                public_bootstrap_order=_pub_bootstrap_order,
                keyword_seed_fallback_triggered=_keyword_seed_fallback,
                public_bootstrap_prevented_discovery_timeout=_pub_bootstrap_prevented,
                public_bootstrap_first_fetch_attempted=_pub_bootstrap_fetch_att,
                public_stage_counters=self._build_public_stage_counters(),
                ct_bridge_rejections_count=getattr(self._result, "ct_bridge_rejections_count", 0),
                ct_storage_rejected=getattr(self._result, "ct_storage_rejected", 0),
                arrow_last_flush_error=getattr(self._result, "arrow_last_flush_error", "") or "",
                arrow_batch_dropped=getattr(self._result, "arrow_batch_dropped_after_flush_failure", 0),
                prewindup_barrier_errors=getattr(self._result, "prewindup_barrier_errors", None) or {},
                return_guard_errors=getattr(self._result, "return_guard_errors", None) or {},
                wayback_unchanged_rejected=getattr(self._result, "wayback_unchanged_rejected", 0),
                nonfeed_provider_failures=getattr(self._result, "nonfeed_provider_failures", None) or [],
                quality_rejection_summary_by_family=getattr(self._result, "quality_rejection_summary_by_family", None)
                or {},
                duplicate_rejection_summary_by_family=getattr(
                    self._result, "duplicate_rejection_summary_by_family", None
                )
                or {},
                low_information_by_family=getattr(self._result, "low_information_by_family", None) or {},
                nonfeed_candidate_ledger_summary=self._nonfeed_ledger.summary()
                if hasattr(self, "_nonfeed_ledger")
                and self._nonfeed_ledger is not None
                and hasattr(self._nonfeed_ledger, "summary")
                else {},
                acquisition_plan_build_failed=getattr(self._result, "acquisition_plan_build_failed", False),
                acquisition_plan_build_error_type=getattr(self._result, "acquisition_plan_build_error_type", ""),
                acquisition_plan_build_error=getattr(self._result, "acquisition_plan_build_error", ""),
                seed_context_available=getattr(self._result, "seed_context_available", False),
                seed_context_propagated=getattr(self._result, "seed_context_propagated", False),
                seed_context_skip_reason=getattr(self._result, "seed_context_skip_reason", ""),
                seed_context_source=getattr(self._result, "seed_context_source", ""),
                lanes_unlocked_by_seed_context=getattr(self._result, "lanes_unlocked_by_seed_context", []) or [],
                public_provider_selection_debug=getattr(self._result, "public_provider_selection_debug", None) or {},
            )
        except Exception:  # noqa: BLE001 — best-effort; lock acquisition failure; non-critical
            pass
        return report

    _BASE_TIER_WEIGHTS: dict[str, float] = {"structured_ti": 1.0, "clearnet": 0.8, "academic": 0.6, "dark": 1.2}

    async def load_source_weights(self, store: Any) -> None:
        """

        Load hit-rate history from DuckDB and set source weights.



        Bounds: 0.3 - 2.5 (30% floor, 250% ceiling, B.6).

        Falls back to defaults on any error.

        """
        try:
            rows = await store.async_query_sprint_source_stats()
            if not rows:
                return
            max_rate = max((r["avg_hit_rate"] for r in rows)) or 1.0
            for row in rows:
                src = row["source_type"]
                raw = row["avg_hit_rate"] / max_rate * 1.5
                clipped = max(0.3, min(2.5, raw))
                get_sprint_ctx().source_weights[src] = clipped
                log.debug(f"Source weight {src}: {clipped:.2f}")
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning(f"Source weight load failed: {e} -- using defaults")

    @staticmethod
    def _adapt_source_weights_from_feedback_python(
        source_quality_feedback: dict[str, dict[str, int]],
        config: Any,
        source_weights: dict[str, float],
        log: logging.Logger,
    ) -> None:
        """
        Pure-Python implementation of source weight adaptation.
        Extracted to a standalone function so it can be used as a fallback
        when the Rust NEON backend is unavailable.

        Matches exactly the F199A adaptation rules:
          - ratio >= 0.7  -> delta 1.10 (+10%)
          - ratio >= 0.4  -> delta 1.05 (+5%)
          - ratio >= 0.15 -> delta 1.00 (neutral)
          - ratio <  0.15 -> delta 0.95 (-5%)
        Clamped to [0.3, 2.5] per B.6.
        """
        for feed_url, fb in source_quality_feedback.items():
            total = fb.get("fetched", 0)
            accepted = fb.get("accepted", 0)
            if total == 0:
                continue
            source_type = config.tier_of(feed_url).name.lower()
            current = source_weights.get(source_type, 1.0)
            ratio = accepted / total
            if ratio >= 0.7:
                delta = 1.1
            elif ratio >= 0.4:
                delta = 1.05
            elif ratio >= 0.15:
                delta = 1.0
            else:
                delta = 0.95
            new_weight = max(0.3, min(2.5, current * delta))
            source_weights[source_type] = new_weight
            log.debug(
                f"[F199A][Python] Source weight adaptation: {source_type} ({accepted}/{total}={ratio:.2%}) {current:.3f} -> {new_weight:.3f}"
            )

    def _adapt_source_weights_from_feedback(self) -> None:
        """

        F199A: Adapt _source_weights from per-source quality feedback collected during the sprint.



        Called at teardown (in run() after cycles complete). Updates each feed_url's weight

        based on accepted/total ratio signal collected via _process_result().



        Adaptation rule (B.6 bounds ±20% per sprint -> clamp to [0.3, 2.5]):

          - accepted/total >= 0.7 -> reward: +10%

          - accepted/total >= 0.4 -> reward: +5%

          - accepted/total >= 0.15 -> reward: 0 (neutral)

          - accepted/total < 0.15 -> penalty: -5%

          - no signal (total=0) -> no change



        Signal is per-feed_url (feed_url as key), not per-source_type.

        For scoring, feed_url maps to source_type via _config.tier_of(feed_url).name.

        """
        source_type_map: list[tuple[str, str]] = []
        stats_list: list[dict[str, float]] = []
        for feed_url, fb in self._source_quality_feedback.items():
            total = fb.get("fetched", 0)
            accepted = fb.get("accepted", 0)
            if total == 0:
                continue
            source_type = self._config.tier_of(feed_url).name.lower()
            ctx = get_sprint_ctx()
            current = ctx.source_weights.get(source_type, 1.0)
            source_type_map.append((source_type, feed_url))
            stats_list.append(
                {"fetched": float(total), "accepted": float(accepted), "current_weight": current, "novelty": False}
            )
        if batch_compute_scores is not None and stats_list:
            try:
                new_weights: list[float] = batch_compute_scores(stats_list)
                ctx = get_sprint_ctx()
                for i, (source_type, feed_url) in enumerate(source_type_map):
                    new_weight = new_weights[i]
                    ctx.source_weights[source_type] = new_weight
                    fb = self._source_quality_feedback[feed_url]
                    log.debug(
                        f"[F199A][NEON] Source weight adaptation: {source_type} ({int(fb['accepted'])}/{int(fb['fetched'])}) {stats_list[i]['current_weight']:.3f} -> {new_weight:.3f}"
                    )
            except Exception as e:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
                log.debug(f"[F199A][NEON] batch_compute_scores failed: {e} -- falling back to Python")
                SprintScheduler._adapt_source_weights_from_feedback_python(
                    self._source_quality_feedback, self._config, get_sprint_ctx().source_weights, log
                )
        elif stats_list:
            SprintScheduler._adapt_source_weights_from_feedback_python(
                self._source_quality_feedback, self._config, get_sprint_ctx().source_weights, log
            )

    def score_source(self, source_type: str, ioc_graph_stats: dict | None = None) -> float:
        """

        Compute priority score per B.1 formula.



        score(source) = base_tier_weight(source)

                      * hit_rate_multiplier(source)

                      * novelty_bonus(source)

        """
        base = self._BASE_TIER_WEIGHTS.get(source_type, 0.7)
        hit_mult = get_sprint_ctx().source_weights.get(source_type, 1.0)
        novelty = get_sprint_ctx().novelty_bonuses.get(source_type, 1.0)
        return base * hit_mult * novelty

    def prioritize_sources(self, candidates: list[str], ioc_graph_stats: dict | None = None) -> list[str]:
        """

        Sort candidates by score -- highest first.

        Returns list of source_type strings ordered by priority.

        """
        scored = [(src, self.score_source(src, ioc_graph_stats)) for src in candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        log.debug(f"Source priorities: {[(s, f'{sc:.2f}') for s, sc in scored[:5]]}")
        return [s for s, _ in scored]

    def set_novelty_bonus(self, source_type: str, has_bonus: bool) -> None:
        """Set novelty bonus: 1.5 if source added new IOC types this sprint."""
        get_sprint_ctx().novelty_bonuses[source_type] = 1.5 if has_bonus else 1.0

    def _update_latency_ema(self, domain: str, latency: float) -> None:
        """Update EMA for domain fetch latency. Bounded to _MAX_FETCH_LATENCY_EMA entries."""
        ctx = get_sprint_ctx()
        prev = ctx.fetch_latency_ema.get(domain, latency)
        ctx.fetch_latency_ema[domain] = 0.3 * latency + 0.7 * prev
        if domain not in self._fetch_latency_ema_order:
            self._fetch_latency_ema_order.append(domain)

    def get_adaptive_timeout(self, domain: str) -> float:
        """Get adaptive timeout based on EMA latency. Clamped to [5, 30]s."""
        ctx = get_sprint_ctx()
        ema = ctx.fetch_latency_ema.get(domain, 10.0)
        return max(5.0, min(30.0, ema * 3.0))

    async def log_source_hit(
        self, store: Any, sprint_id: str, source_type: str, findings_count: int, ioc_count: int
    ) -> None:
        """Record a source hit for hit-rate tracking."""
        hit_rate = findings_count / max(1, findings_count + 1)
        try:
            await store.async_record_source_hit(
                sprint_id, _time.time(), source_type, findings_count, ioc_count, hit_rate
            )
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.warning(f"source_hit_log insert failed: {e}")

    def inject_ioc_graph(self, ioc_graph: Any) -> None:
        """Inject IOCGraph reference for pivot operations.

        F300-GRAPH: DuckPGQGraph is the sole canonical graph backend.
        KuzuGraphBridge wiring removed — it is no longer used.
        """
        self._pivot_ioc_graph = ioc_graph

    def get_graph(self) -> Any:
        """Get IOC graph for read operations (stats, export, injection).

        Returns the DuckPGQGraph instance used for analytics.
        Used by windup_engine and other consumers that need graph access.
        """
        return getattr(self, "_ioc_graph", None)

    def inject_policy_manager(self, policy_manager: Any) -> None:
        """Inject SprintPolicyManager reference (opt-in RL layer)."""
        self._policy_manager = policy_manager
        if hasattr(policy_manager, "inject_scheduler"):
            policy_manager.inject_scheduler(self)

    def inject_communication_layer(self, layer: Any) -> None:
        """Inject CommunicationLayer reference (F26X-3, advisory, default-OFF).

        Caller (core/__main__.py) wires a CommunicationLayer produced by
        layers.get_communication_layer() unless --no-communication is set.
        None injection is allowed (caller may pass None as a no-op or to
        clear a previously injected layer).
        All advisory call sites are guarded by `if self._communication_layer
        is not None:` and wrapped in try/except (fail-soft, M1 invariant).
        """
        self._communication_layer = layer

    def inject_stealth_layer(self, layer: Any) -> None:
        """Inject StealthLayer reference (F260, advisory, default-OFF).

        Caller (core/__main__.py) wires a StealthLayer produced by
        layers.get_stealth_layer() unless --no-stealth is set. None injection
        is allowed (caller may pass None as a no-op or to clear a previously
        injected layer). All advisory call sites are guarded by
        `if self._stealth_layer is not None:` and wrapped in try/except
        (fail-soft, M1 invariant).
        """
        self._stealth_layer = layer
        from core.telemetry.context_state import set_stealth_enabled as _set_stealth

        _set_stealth(layer is not None)

    def inject_ghost_layer(self, layer: Any) -> None:
        """Inject GhostLayer reference (F260, advisory, default-OFF).

        Caller (core/__main__.py) wires a GhostLayer produced by
        layers.get_ghost_layer() unless --no-ghost is set. None injection is
        allowed (caller may pass None as a no-op or to clear a previously
        injected layer). All advisory call sites are guarded by
        `if self._ghost_layer is not None:` and wrapped in try/except
        (fail-soft, M1 invariant).
        """
        self._ghost_layer = layer

    def inject_prefetch_oracle(self, oracle: Any) -> None:
        """

        Inject PrefetchOracleIntegration reference (advisory prefetch ordering).



        F200A: oracle is ADVISORY ONLY -- scheduler retains all authority.

        Oracle suggests sort scores; scheduler multiplies them into economics sort key.

        All oracle calls are fail-soft -- exception or None oracle -> no-op.

        """
        self._prefetch_oracle = oracle

    def inject_prefetch_pipeline(self, pipeline: Any) -> None:
        """

        P3-3: Inject ContinuousPrefetchPipeline reference.



        Pipeline runs producer-consumer pattern for speculative IOC prefetching.
        Starts automatically with sprint if injected.
        """
        self._prefetch_pipeline = pipeline

    def inject_temporal_predictor(self, predictor: Any) -> None:
        """
        P3-2: Inject TemporalIOCPredictor reference.

        Predictor observes findings for time-of-day pattern learning
        and provides predict_next_iocs() for ContinuousPrefetchPipeline.
        """
        self._temporal_predictor = predictor

    def get_prefetch_pipeline_stats(self) -> dict[str, Any] | None:
        """

        P3-3: Return pipeline statistics if pipeline is injected.

        """
        if self._prefetch_pipeline is None:
            return None
        return self._prefetch_pipeline.get_stats()

    def inject_pivot_planner(self, planner: Any) -> None:
        """

        Inject PivotPlanner reference (F202G advisory pivot ordering).



        F202G: planner is ADVISORY ONLY -- scheduler retains all authority.

        Planner generates pivot suggestions from findings; scheduler uses them

        as advisory ordering input, NOT as new sprint owner.

        All planner calls are fail-soft -- exception or None planner -> no-op.

        """
        self._pivot_planner = planner

    def inject_analyst_workbench(self, workbench: Any) -> None:
        """

        F204E: Inject AnalystWorkbench reference for sprint brief generation.



        Workbench is used at TEARDOWN to generate a model-free analyst brief

        summarizing sprint results: what changed, strongest evidence,

        next best pivots, and open questions.



        All workbench calls are fail-soft -- exception or None workbench -> no-op brief.

        """
        self._analyst_workbench = workbench

    def inject_forensics_enricher(self, enricher: Any, lmdb_env: Any = None) -> None:
        """

        F195C: Inject ForensicsEnricher + LMDB env (external wiring).



        OWNERSHIP: caller owns enricher lifecycle. Scheduler invokes

        enricher.enrich() during finding sidecar processing. LMDB env

        is owned by caller and passed here for reference only.

        All calls are fail-soft -- exception or None -> no-op.

        """
        self._forensics_enricher = enricher
        self._forensics_lmdb_env = lmdb_env

    def inject_multimodal_enricher(self, enricher: Any, lmdb_env: Any = None) -> None:
        """

        F195C: Inject MultimodalEnricher + LMDB env (external wiring).



        OWNERSHIP: caller owns enricher lifecycle. Scheduler invokes

        enricher.enrich() during finding sidecar processing. LMDB env

        is owned by caller and passed here for reference only.

        All calls are fail-soft -- exception or None -> no-op.

        """
        self._multimodal_enricher = enricher
        self._multimodal_lmdb_env = lmdb_env

    def inject_enrichment_services(self, services: Any) -> None:
        """F350M: Inject EnrichmentServices (forensics + multimodal unified lifecycle)."""
        self._enrichment_services = services

    def inject_evidence_log(self, elog: Any) -> None:
        """F11C: Inject EvidenceLog reference (fail-safe, M1 8GB safe)."""
        self._evidence_log = elog

    def inject_source_economics(self, economics: dict[str, SourceEconomics]) -> None:
        """

        F160C: Inject pre-built source economics map (external wiring).



        OWNERSHIP: caller owns the economics map. Scheduler updates it

        via _update_source_economics() during sprint execution.

        Pass None or empty dict to use scheduler's internal dict (default).

        """
        if economics is not None:
            self._source_economics = economics

    def inject_duckdb_store(self, store: Any) -> None:
        """

        F195: Inject DuckDB store reference (canonical write seam).



        OWNERSHIP: caller owns the store. Scheduler uses it for

        async_ingest_findings_batch() on accepted findings.

        All calls are fail-soft -- exception or None -> no-op.

        """
        self._duckdb_store = store
        self._duckdb_can_ingest = store is not None and hasattr(store, "async_ingest_findings_batch")

    def inject_privacy_layer(self, layer: Any) -> None:
        """

        F26X: Inject PrivacyLayer reference for PII gate.

        Preferred over self._layer_manager.privacy -- removes the 7-site
        lazy init scattering and makes the dependency explicit.

        Fallback: if not injected, the helper still consults
        self._layer_manager.privacy (legacy path). Never raises --
        exception or None -> no-op (same as other inject_* methods).

        OWNERSHIP: caller owns the layer. Scheduler uses it for
        _run_privacy_gate() before every async_ingest_findings_batch()
        call when HLEDAC_ENABLE_PRIVACY_LAYER=1.
        """
        self._privacy_layer = layer

    def inject_security_coordinator(self, coordinator: Any) -> None:
        """
        F26X+: Inject UniversalSecurityCoordinator for multi-layer security.


        Coordinates: StealthEngine, ThreatIntelligence, QuantumCrypto, ZKP.
        Security levels: MINIMAL(1) → STANDARD(2) → HIGH(3) → MAXIMUM(4).

        OWNERSHIP: caller owns the coordinator. Scheduler uses it for
        _run_security_session() in research/aggressive sprint modes.
        """
        self._security_coordinator = coordinator

    def get_analyst_brief(self) -> Any:
        """

        F204E: Return the last generated analyst brief.



        Returns None if no brief was generated or brief generation failed.

        """
        return getattr(self, "_analyst_brief", None)

    def get_planned_pivots(self) -> list:
        """

        F202G: Return last planned pivots for diagnostics.



        Returns empty list if no pivots were planned or planner failed.

        """
        return getattr(self, "_planned_pivots", [])

    async def _sensitive_query_transport(self) -> str:
        """

        Sprint F250: Return preferred transport for sensitive queries.

        Priority: Nym > Tor > I2P > clearnet.

        Returns transport name string or "clearnet" fallback.

        """
        try:
            from hledac.universal.transport.nym_transport import NymTransport

            nym = NymTransport()
            if nym.is_running and (not getattr(nym, "circuit_breaker_open", False)):
                return "nym"
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        try:
            from hledac.universal.transport.tor_transport import TorTransport

            tor = TorTransport()
            if tor.available and getattr(tor, "_circuit_established", False):
                return "tor"
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        try:
            from hledac.universal.transport.i2p_transport import I2PTransport
            from hledac.universal.transport.transport_resolver import get_i2p_transport_singleton

            singleton = get_i2p_transport_singleton()
            if singleton is not None and await singleton.is_running():
                return "i2p"
            i2p = I2PTransport()
            if await i2p.is_running():
                return "i2p"
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        return "clearnet"

    async def health_check(self) -> HealthReport:
        """

        F228F: Pre-run health check for critical dependencies.
        F270-4.3: Cached -- returns same report within same active sprint cycle.
        Always returns HealthReport -- NEVER raises.
        Timeout handled externally by caller (asyncio.timeout in __main__).

        """
        if self._health_cache is not None:
            cached_report, cached_sprint_id = self._health_cache
            current_sprint_id = getattr(self, "_sprint_id", None)
            if cached_sprint_id == current_sprint_id:
                return cached_report
        log = get_logger("hledac.sprint")
        errors: list[str] = []
        duckdb_ok = False
        hermes_ok = False
        fetch_coordinator_ok = False
        graph_service_ok = False
        duckdb_locked = False
        evidence_log_ok = False
        memory_pressure_ok = True
        try:
            store = getattr(self, "_duckdb_store", None)
            if store is not None and hasattr(store, "async_ingest_findings_batch"):
                try:
                    is_closed = _safe_getattr(store, "is_closed", False)
                    startup_ready = _safe_getattr(store, "startup_ready", False)
                    duckdb_locked = bool(is_closed) or not bool(startup_ready)
                except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                    duckdb_locked = True
                if not duckdb_locked:
                    duckdb_ok = True
                else:
                    errors.append("duckdb_store is locked or not initialized")
            else:
                errors.append("duckdb_store not injected or missing async_ingest_findings_batch")
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            errors.append(f"duckdb check failed: {e}")
        try:
            evidence_log = getattr(self, "_evidence_log", None)
            if evidence_log is not None:
                is_frozen = _safe_getattr(evidence_log, "is_frozen", False)
                if is_frozen:
                    errors.append("evidence_log is frozen (connection closed)")
                else:
                    evidence_log_ok = True
            else:
                evidence_log_ok = True
        except Exception as e:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
            errors.append(f"evidence_log check failed: {e}")
            evidence_log_ok = False
        try:
            M1_MEMORY_WARNING_THRESHOLD = 5.5 * 1024**3
            M1_MEMORY_CRITICAL_THRESHOLD = 6.0 * 1024**3
            current_rss = getattr(self, "_current_rss_mb", 0) * 1024 * 1024
            if current_rss > M1_MEMORY_CRITICAL_THRESHOLD:
                memory_pressure_ok = False
                errors.append(f"memory critical: RSS={current_rss / 1024**3:.1f}GiB > 6.0GiB")
            elif current_rss > M1_MEMORY_WARNING_THRESHOLD:
                memory_pressure_ok = True
                log.warning(f"[F265.1] memory pressure warning: RSS={current_rss / 1024**3:.1f}GiB > 5.5GiB")
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            memory_pressure_ok = True
        try:
            result = getattr(self, "_result", None)
            hermes_load_attempted = result.hermes_load_attempted if result else False
            _hermes_load_reason = result.hermes_load_reason if result else ""
            hermes_synthesis_enabled = ENV.get_bool("HLEDAC_ENABLE_HERMES_SYNTHESIS")
            if hermes_load_attempted:
                hermes_ok = result.hermes_model_loaded if result else False
            elif not hermes_synthesis_enabled:
                hermes_ok = True
            else:
                hermes_ok = False
                errors.append("hermes prewarm not yet run")
        except Exception as e:  # noqa: BLE001 — best-effort; prewarm failure; non-critical
            errors.append(f"hermes check failed: {e}")
        try:
            from hledac.universal.fetching.public_fetcher import get_public_fetcher_session_status

            status = get_public_fetcher_session_status()
            fetch_coordinator_ok = (
                status.get("tor_session_present", False)
                and (not status.get("tor_session_closed", True))
                or (status.get("i2p_session_present", False) and (not status.get("i2p_session_closed", True)))
            )
            if not fetch_coordinator_ok:
                errors.append("public_fetcher sessions not available")
        except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
            fetch_coordinator_ok = False
        try:
            from hledac.universal.knowledge.graph_service import graph_stats

            stats = graph_stats()
            graph_service_ok = stats is not None
        except Exception:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
            graph_service_ok = False
        nym_circuit_open = False
        try:
            from hledac.universal.transport.nym_transport import NymTransport

            nym = NymTransport()
            nym_circuit_open = _safe_getattr(nym, "circuit_breaker_open", False)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        overall_ok = duckdb_ok and hermes_ok and evidence_log_ok
        blocking_ok = duckdb_ok and graph_service_ok and memory_pressure_ok
        report = HealthReport(
            duckdb_ok=duckdb_ok,
            hermes_ok=hermes_ok,
            fetch_coordinator_ok=fetch_coordinator_ok,
            graph_service_ok=graph_service_ok,
            nym_circuit_open=nym_circuit_open,
            evidence_log_ok=evidence_log_ok,
            memory_pressure_ok=memory_pressure_ok,
            overall_ok=overall_ok,
            blocking_ok=blocking_ok,
            errors=errors,
        )
        if blocking_ok:
            if overall_ok:
                log.info(f"[F228F] Health check: {report.summary()}")
            else:
                log.info(f"[F228F] Health check: {report.summary()} (advisory-degraded, non-blocking: {errors})")
        else:
            log.warning(f"[F228F] Health check BLOCKING-DEGRADED: {errors}")
        current_sprint_id = getattr(self, "_sprint_id", None)
        self._health_cache = (report, current_sprint_id)
        return report

    def _invalidate_health_cache(self) -> None:
        """F270-4.3: Invalidate health_check cache (call on sprint start/end)."""
        self._health_cache = None

    def enqueue_pivot(
        self, ioc_value: str, ioc_type: str, confidence: float, degree: float = 1.0, task_type: str | None = None
    ) -> None:
        """Sprint F-EXTRACT-2: Delegation wrapper to FetchCoordinator.enqueue_pivot.

        Original 104-LOC implementation moved to
        coordinators/fetch_coordinator.py (GOD_OBJECT_ANALYSIS Phase 2).
        100% backward compatibility preserved -- no caller code change.

        State (`_pivot_queue`, `_pivot_stats`) and helper
        (`_get_adaptive_priority`) accessed via provider callbacks
        supplied at FetchCoordinator construction. PivotTask imported
        lazily in coordinator to break circular dependency.
        """
        return self._fetch_coordinator.enqueue_pivot(
            ioc_value=ioc_value, ioc_type=ioc_type, confidence=confidence, degree=degree, task_type=task_type
        )

    def enqueue_hypothesis_pivot(
        self, ioc_value: str, ioc_type: str = "hypothesis", confidence: float = 0.7, depth: int = 1
    ) -> bool:
        """Sprint F-EXTRACT-2: Delegation wrapper to FetchCoordinator.enqueue_hypothesis_pivot.

        Original 76-LOC implementation moved to
        coordinators/fetch_coordinator.py. 100% backward compatibility.

        State (`_hypothesis_query_count`, `_hypothesis_depth`) and cap
        values (`_config.max_hypothesis_depth/queries`) accessed via
        provider callbacks. Cap check logs and IOC enqueue happen in
        the coordinator. Setters (lambda) in the coordinator mutate
        the underlying SprintScheduler attributes via setattr, so
        `scheduler._hypothesis_query_count == N` assertions in F193B
        tests still hold.

        This method is passed as a callback to the public pipeline
        (Sprint F193B) and to windup_engine (BoundedW hypothesis
        loop). Keeping it on SprintScheduler (as a thin wrapper)
        avoids breaking those callback contracts.
        """
        return self._fetch_coordinator.enqueue_hypothesis_pivot(
            ioc_value=ioc_value, ioc_type=ioc_type, confidence=confidence, depth=depth
        )

    async def _drain_pivot_queue(self, max_tasks: int = 5) -> int:
        """

        Drain up to max_tasks from pivot queue. Max 8s total deadline.

        Called at end of each ACTIVE cycle.

        """
        processed = 0
        deadline = _time.monotonic() + 8.0
        while processed < max_tasks:
            if _time.monotonic() > deadline:
                break
            try:
                task = self._pivot_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                async with asyncio.timeout(6.0):
                    await self._execute_pivot(task)
                get_sprint_ctx().pivot_stats["processed"] += 1
            except (TimeoutError, Exception) as e:
                get_sprint_ctx().pivot_stats["errors"] += 1
                log.debug(f"pivot {task.task_type} {task.ioc_value}: {e}")
            processed += 1
        return processed

    async def _execute_pivot(self, task: PivotTask) -> None:
        """Dispatch pivot task to appropriate intelligence client."""
        from hledac.universal.tool_registry import get_task_handler

        handler = get_task_handler(task.task_type)
        if handler is not None:
            await handler(task, self)
            return
        elif task.task_type == "hypothesis_probe":
            words = task.ioc_value.split()
            queries = sorted({w.lower() for w in words if len(w) > 5}, key=len, reverse=True)[:3]
            count_before = getattr(self, "_finding_count", 0)
            for sq in queries:
                self.enqueue_pivot(ioc_value=sq, ioc_type="url", confidence=0.7)
            count_after = getattr(self, "_finding_count", 0)
            hyp_found = count_after - count_before
            if hyp_found > 0:
                try:
                    for ioc_entry in get_sprint_ctx().recent_iocs[-hyp_found:]:
                        ioc_val = ioc_entry.get("value") or ioc_entry.get("ioc", "")
                        if ioc_val:
                            _DEFAULT_GRAPH_SERVICE.upsert_relation(
                                task.ioc_value[:100],
                                ioc_val,
                                rel_type="confirmed_by",
                                weight=0.8,
                                evidence="hypothesis_probe",
                            )
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass
        elif task.task_type == "sprint_windup":
            pass
        else:
            log.debug(f"[DISPATCH] Unknown task type: {task.task_type}")

    _apt_onion_seeder: Any = None

    def _get_apt_onion_seeder(self) -> Any:
        """Lazily instantiate AptOnionSeeder backed by apt_onion_mapping.yaml.

        ISSUE-5 FIX: Replaces hardcoded _KNOWN_APT_ONION_DOMAINS substring match with
        YAML-backed, confidence-scored mapping. Zero-code-update lifecycle: edit
        config/apt_onion_mapping.yaml to add/remove/retire actor→domain mappings.
        """
        if self._apt_onion_seeder is None:
            from hledac.universal.intel.intel_seed import AptOnionSeeder

            self._apt_onion_seeder = AptOnionSeeder()
        return self._apt_onion_seeder

    def _ooda_apt_domain_mapping(self, query: str) -> list[str]:
        """Map threat actor names to .onion infrastructure candidates for OODA bootstrap.

        ISSUE-5 FIX: Uses AptOnionSeeder (YAML backend) instead of hardcoded dict.
        Only returns confirmed + plausible domains (confidence >= 0.7).
        No substring match — requires full token match.
        """
        if not query:
            return []
        seeder = self._get_apt_onion_seeder()
        candidates = seeder.get_candidates_for_query(query, min_confidence=0.7)
        return [domain for domain, _ in candidates]

    async def _buffer_ioc_pivot(self, ioc_type: str, ioc_value: str, confidence: float) -> None:
        """Wrapper: buffer IOC to graph and enqueue for further pivoting."""
        if self._graph_accumulator is None:
            from hledac.universal.runtime.graph_accumulator import SprintGraphAccumulator

            self._graph_accumulator = SprintGraphAccumulator()
        self._graph_accumulator.buffer_pivot_relation(ioc_value, ioc_type, confidence)
        if self._pivot_ioc_graph is not None:
            await self._pivot_ioc_graph.buffer_ioc(ioc_type, ioc_value, confidence)
            degree = 2
            self.enqueue_pivot(ioc_value, ioc_type, confidence * 0.9, degree)

    async def _speculative_prefetch(self, n: int = 3) -> None:
        """Spustit top-n pivot tasků spekulativně jako background tasks."""
        if self._pivot_queue.empty():
            return
        if len(self._speculative_results) > 500:
            keys = list(self._speculative_results.keys())
            for k in keys[:250]:
                del self._speculative_results[k]
        peeked = []
        try:
            with self._pivot_queue.mutex:
                peeked = list(self._pivot_queue.queue)[:n]
        except AttributeError:
            peeked = []
            for _ in range(min(n, self._pivot_queue.qsize())):
                try:
                    item = self._pivot_queue.get_nowait()
                    peeked.append(item)
                    self._pivot_queue.put_nowait(item)
                except asyncio.QueueEmpty:
                    break
                except asyncio.QueueFull:
                    break
        for pivot_task in peeked[:n]:
            task_key = f"{pivot_task.task_type}:{pivot_task.ioc_value}"
            if task_key in self._speculative_results:
                continue

            async def _speculative_run(pt=pivot_task, key=task_key):
                try:
                    result = await self._execute_pivot(pt)
                    self._speculative_results[key] = result or {}
                    log.debug(f"Speculative hit: {key}")
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    log.debug(f"Speculative miss {key}: {e}")

            task = safe_create_task(_speculative_run(), name="sprint:speculative_run")
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

    async def _speculative_dns_prefetch(self, domains: list[str]) -> None:
        """
        Fire-and-forget DNS resolution for top-k domain candidates.

        Runs as background task while fetch loop is active -- overlaps
        DNS latency (~5-50ms) with ongoing network I/O.

        Results stored in _speculative_dns_cache for later pivot planning.
        Fail-soft: any error silently ignored, cache miss treated as "unresolved".

        Args:
            domains: List of domain strings to prefetch
        """
        if not domains:
            return
        import socket as _socket

        async def _resolve_one(domain: str) -> tuple[str, list[str]] | None:
            try:

                def _sync_resolve() -> list[str]:
                    try:
                        addrs = _socket.getaddrinfo(domain, 443, _socket.AF_INET)
                        return list({r[4][0] for r in addrs})
                    except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        return []

                ips = await asyncio.to_thread(_sync_resolve)
                if ips:
                    return (domain, ips)
            except Exception:  # noqa: BLE001 — best-effort; thread operation failure; non-critical
                pass
            return None

        tasks = [_resolve_one(d) for d in domains[:5]]
        results: list = await safe_gather_ok(*tasks, label="sprint_scheduler:_speculative_dns_resolve")
        for r in results:
            if isinstance(r, tuple) and len(r) == 2:
                dom, ips = r
                self._speculative_dns_cache[dom] = ips

    def get_speculative_dns(self, domain: str) -> list[str] | None:
        """
        Retrieve prefetched DNS results for a domain.

        Returns IP list if prefetch hit, None if miss/unresolved.
        Used by pivot planner to skip redundant DNS lookups.
        """
        return getattr(self, "_speculative_dns_cache", {}).get(domain)

    async def _run_ooda_cycle(self, ioc_graph) -> None:
        """Jeden OODA cyklus -- 60s interval."""
        log.info("OODA: cycle start")
        _graph = ioc_graph
        if _graph is None:
            _graph = getattr(self, "_pivot_ioc_graph", None)
        if _graph is None:
            _graph = getattr(self, "_ioc_graph", None)
        _graph_source = (
            "param" if ioc_graph else "_pivot_ioc_graph" if getattr(self, "_pivot_ioc_graph", None) else "_ioc_graph"
        )
        node_count = 0
        edge_count = 0
        try:
            if _graph and hasattr(_graph, "stats"):
                _stats = _graph.stats()
                node_count = _stats.get("nodes", 0) if isinstance(_stats, dict) else 0
                edge_count = _stats.get("edges", 0) if isinstance(_stats, dict) else 0
            log.debug(f"OODA Observe: {node_count} nodes, {edge_count} edges [src={_graph_source}]")
        except Exception:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
            node_count = 0
        top_nodes: list = []
        try:
            if _graph and hasattr(_graph, "get_top_nodes_by_degree"):
                raw_nodes = await asyncio.to_thread(_graph.get_top_nodes_by_degree, 10)
                for n in (raw_nodes or [])[:10]:
                    if isinstance(n, dict):
                        val = n.get("value", "")
                        ioc_type = n.get("ioc_type", "unknown")
                        degree = float(n.get("degree", 0))
                        if val:
                            top_nodes.append((val, ioc_type, degree))
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug(f"OODA Orient degree ranking: {e}")
        _bootstrap_seeds: list[tuple] = []
        if node_count < 3 or edge_count == 0:
            _query_text = getattr(self, "_query", "") or ""
            if _query_text:
                try:
                    _cands = extract_domain_candidates_from_text(
                        _query_text, source_family="PUBLIC", min_confidence=0.2
                    )
                    for c in _cands[:5]:
                        if hasattr(c, "domain") and c.domain:
                            _bootstrap_seeds.append((c.domain, "domain", c.confidence))
                    log.debug(f"OODA bootstrap from query: {len(_bootstrap_seeds)} domains")
                except Exception:  # noqa: BLE001 — best-effort; DB write/query failure; non-critical
                    pass
                if not _bootstrap_seeds:
                    _apt_candidates = self._ooda_apt_domain_mapping(_query_text)
                    for _apt_dom in _apt_candidates:
                        _bootstrap_seeds.append((_apt_dom, "onion", 0.5))
                    if _apt_candidates:
                        log.debug(f"OODA APT mapping: {_apt_candidates}")
            if len(_bootstrap_seeds) < 3 and getattr(self, "_duckdb_store", None) is not None:
                try:
                    _sprint_id = getattr(self, "sprint_id", "") or ""
                    _safe_id = _sprint_id.replace("'", "''")
                    _sql = f"SELECT payload_text FROM findings WHERE sprint_id = '{_safe_id}' LIMIT 50"
                    _rows = self.query_sprint_results(_sql)
                    for _row in _rows[:20]:
                        _text = _row.get("payload_text", "") or ""
                        if _text and isinstance(_text, str):
                            try:
                                _cands = extract_domain_candidates_from_text(
                                    _text, source_family="PUBLIC", min_confidence=0.2
                                )
                                for c in _cands[:3]:
                                    if hasattr(c, "domain") and c.domain:
                                        _bootstrap_seeds.append((c.domain, "domain", c.confidence))
                            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                                pass
                    log.debug(f"OODA bootstrap from DuckDB: {len(_bootstrap_seeds)} domains")
                except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                    pass
        if len(top_nodes) < 3 and _bootstrap_seeds:
            _seen: set[tuple] = set(top_nodes)
            for _seed in _bootstrap_seeds[:5]:
                if _seed not in _seen:
                    top_nodes.append(_seed)
                    _seen.add(_seed)
                    if len(top_nodes) >= 5:
                        break
        decided_seeds: list = []
        for node in top_nodes[:5]:
            if len(node) >= 3:
                value, ioc_type, degree = (node[0], node[1], float(node[2]))
            else:
                continue
            pr_score = degree
            if pr_score > 0:
                confidence = min(0.95, 0.5 + pr_score * 0.05)
                decided_seeds.append((value, ioc_type, confidence))
        acted = 0
        for value, ioc_type, confidence in decided_seeds:
            try:
                self.enqueue_pivot(value, ioc_type, confidence, degree=2)
                acted += 1
            except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.debug(f"OODA Act enqueue {value}: {e}")
        ctx = get_sprint_ctx()
        ctx.pivot_stats["ooda_cycles"] = ctx.pivot_stats.get("ooda_cycles", 0) + 1
        ctx.pivot_stats["ooda_last_acted"] = acted
        ctx.pivot_stats["ooda_nodes"] = node_count
        ctx.pivot_stats["ooda_edges"] = edge_count
        ctx.pivot_stats["ooda_graph_source"] = _graph_source
        log.info(f"OODA: acted on {acted} nodes [nodes={node_count} edges={edge_count} src={_graph_source}]")

    @property
    def _arrow_flush_n(self) -> int:
        """Dynamically resolve Arrow flush N based on UMA state.

        F26X-I: critical/emergency = 2500, warn = 1500, ok = 1000.
        Read from _governor at call time (not init), so late binding is safe.
        """
        raw = ENV.get_int("HLEDAC_ARROW_FLUSH_N")
        if raw:
            return max(100, min(raw, 10000))
        uma_state = getattr(self, "_governor", None) and getattr(self._governor, "_uma_state", "ok") or "ok"
        if uma_state in ("critical", "emergency"):
            return 2500
        elif uma_state == "warn":
            return 1500
        return 1000

    def _resolve_arrow_batch_hard_cap(self) -> int:
        """Resolve Arrow batch hard cap from env or return M1-safe default.



        F214OPT-D: Prevents unbounded Arrow batch growth after flush failure.

        Default is max(2 * _ARROW_FLUSH_N, 2000) = 2000 entries (~10MB range).

        Env override: HLEDAC_ARROW_BATCH_HARD_CAP (min 100, max 50000).

        """
        try:
            raw = ENV.get_int("HLEDAC_ARROW_BATCH_HARD_CAP")
            if raw:
                return max(100, min(raw, 50000))
        except (ValueError, TypeError):
            pass
        return max(2 * self._arrow_flush_n, 2000)

    async def _maybe_flush_to_parquet(self) -> None:
        """Flush Arrow batch to Parquet when N or S threshold is hit.



        F214OPT-D: On flush failure, batch is truncated to HARD_CAP to prevent

        unbounded growth. Failed entries are dropped (oldest first) and counted.

        """
        import time as _time

        now = _time.monotonic()
        if (
            len(get_sprint_ctx().arrow_batch) < self._arrow_flush_n
            and now - self._arrow_last_flush < self._ARROW_FLUSH_S
        ):
            return
        if not get_sprint_ctx().arrow_batch:
            return
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            log.warning("[8VD-PARQUET] pyarrow not available -- skipping flush")
            return
        batch = get_sprint_ctx().arrow_batch[:]
        schema = pa.schema(
            [
                ("url", pa.string()),
                ("title", pa.string()),
                ("snippet", pa.string()),
                ("source", pa.string()),
                ("ioc", pa.string()),
                ("ioc_type", pa.string()),
                ("confidence", pa.float32()),
                ("timestamp", pa.timestamp("ms", tz="UTC")),
                ("sprint_id", pa.string()),
            ]
        )
        rows = {k: [r.get(k) for r in batch] for k in schema.names}
        table = pa.table(rows, schema=schema)
        from hledac.universal.paths import get_sprint_parquet_dir

        sid = self.sprint_id or getattr(self, "sprint_id", "unknown")
        path = get_sprint_parquet_dir(sid) / f"batch_{int(now * 1000)}.parquet"
        try:
            await asyncio.to_thread(pq.write_table, table, path, compression="snappy")
            log.info(f"[8VD-PARQUET] flushed {len(batch)} rows -> {path}")
            get_sprint_ctx().arrow_batch.clear()
            self._arrow_last_flush = now
        except Exception as exc:  # noqa: BLE001 — best-effort; export/write failure; non-critical
            self._result.arrow_last_flush_error = str(exc)[:256]
            batch_len = len(get_sprint_ctx().arrow_batch)
            hard_cap = self._ARROW_BATCH_HARD_CAP
            if batch_len > hard_cap:
                drop_count = batch_len - hard_cap
                get_sprint_ctx().arrow_batch = get_sprint_ctx().arrow_batch[drop_count:]
                self._result.arrow_batch_dropped_after_flush_failure += drop_count
                log.warning(
                    f"[8VD-PARQUET] flush failed ({exc}), dropped {drop_count} oldest entries to enforce HARD_CAP={hard_cap}, {len(get_sprint_ctx().arrow_batch)} remain"
                )
            else:
                log.warning(f"[8VD-PARQUET] flush failed ({exc}), batch intact ({batch_len}), will retry")

    def buffer_finding(self, finding: dict) -> None:
        """Buffer a finding into the Arrow batch."""
        get_sprint_ctx().arrow_batch.append(finding)
        try:
            _t = safe_create_task(self._maybe_flush_to_parquet(), name="sprint:flush_arrow")
            self._bg_tasks.add(_t)
            _t.add_done_callback(self._bg_tasks.discard)
        except RuntimeError:
            pass
        _text = " ".join(
            filter(None, [finding.get("snippet", ""), finding.get("content", ""), finding.get("title", "")])
        ).strip()
        if len(_text) > 10:
            try:
                from hledac.universal.brain.ane_embedder import extract_iocs_from_text

                for ioc in extract_iocs_from_text(_text[:2000]):
                    ioc_entry = {**ioc, "source": "ner_extracted", "parent_url": finding.get("url", "")}
                    self.buffer_ioc(ioc_entry)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass

    def buffer_ioc(self, ioc: dict) -> None:
        """

        Buffer an IOC into the Arrow batch.



        Sprint 8VI §D: IOCScorer final_score zapojeno.

        Sprint 8VI §C: Recent IOC ring buffer pro hypothesis feedback.

        """
        ioc_entry = dict(ioc)
        if hasattr(self, "_ioc_scorer") and self._ioc_scorer is not None:
            try:
                score = self._ioc_scorer.final_score(ioc_entry)
                ioc_entry["confidence"] = score
            except Exception as _exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                log.debug("ioc_scorer.final_score failed: %s", _exc)
        # ISSUE-6: recent_iocs bounded via maxlen=200 deque — no slice assignment needed.
        # Slice assignment `ctx.recent_iocs[-100:]` was O(n) copy on every IOC, replaced
        # by simple append. The deque silently evicts oldest entries at maxlen.
        get_sprint_ctx().recent_iocs.append(ioc_entry)
        get_sprint_ctx().arrow_batch.append(ioc_entry)
        try:
            _t = safe_create_task(self._maybe_flush_to_parquet(), name="sprint:flush_arrow_ioc")
            self._bg_tasks.add(_t)
            _t.add_done_callback(self._bg_tasks.discard)
        except RuntimeError:
            pass

    def _get_duckdb_con(self):
        """Singleton DuckDB connection -- initialized once."""
        if self._duckdb_read_con is None:
            import duckdb

            self._duckdb_read_con = duckdb.connect()
        return self._duckdb_read_con

    def query_sprint_results(self, sql: str) -> list[dict]:
        """DuckDB zero-copy query over Parquet files via Arrow.

        DuckDB + pyarrow (no polars): DuckDB's read_parquet() + fetch_arrow_table()
        gives zero-copy Arrow record batch → pyarrow table → list[dict].
        Polars is NOT needed here — only for in-memory feature engineering (F5.4).
        """
        try:
            arrow_tbl = self._get_duckdb_con().execute(sql).fetch_arrow_table()
            return arrow_tbl.to_pylist()
        except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            try:
                return self._get_duckdb_con().execute(sql).fetchdf().to_dict("records")
            except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                return []

    def deduplicate_and_rank_findings(self, sprint_id: str | None = None) -> str:
        """DuckDB-powered dedup + ranking over Parquet files (F5.4).

        Strategy:
          1. DuckDB SQL aggregation via read_parquet(glob) — zero-copy Arrow,
             M1 RAM-safe streaming, no polars dependency for I/O.
          2. COPY TO Parquet — DuckDB writes directly, no intermediate DataFrame.
          3. Polars only for in-memory ranking when DuckDB COPY is unavailable.

        Fallback chain: DuckDB COPY → polars LazyFrame streaming collect →
        pyarrow fallback. All paths return a valid parquet path.
        """
        from hledac.universal.paths import get_sprint_parquet_dir

        sid = sprint_id or self.sprint_id or "*"
        store_dir = get_sprint_parquet_dir(sid)
        glob = str(store_dir / "batch_*.parquet")
        out = str(store_dir / "ranked.parquet")
        try:
            con = self._get_duckdb_con()
            sql = f"\n                COPY (\n                    SELECT\n                        FIRST(title)     AS title,\n                        FIRST(source)    AS source,\n                        url,\n                        ioc,\n                        MAX(confidence) AS confidence,\n                        COUNT(*)         AS hit_count\n                    FROM read_parquet('{glob}')\n                    WHERE url IS NOT NULL OR ioc IS NOT NULL\n                    GROUP BY url, ioc\n                    ORDER BY hit_count DESC\n                ) TO '{out}' (FORMAT PARQUET, COMPRESSION 'snappy')\n            "
            con.execute(sql)
            return out
        except Exception as _e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            log.debug(f"[F5.4] DuckDB COPY failed: {_e}")
        try:
            import polars as pl

            pl.scan_parquet(glob).filter(pl.col("url").is_not_null() | pl.col("ioc").is_not_null()).with_columns(
                [pl.col("confidence").fill_null(0.5), pl.col("source").cast(pl.Categorical)]
            ).group_by(["url", "ioc"]).agg(
                [
                    pl.col("title").first(),
                    pl.col("source").first(),
                    pl.col("confidence").max(),
                    pl.len().alias("hit_count"),
                ]
            ).sort("hit_count", descending=True).collect(engine="streaming").write_parquet(out, compression="snappy")
            return out
        except Exception:  # noqa: BLE001 — best-effort; export/write failure; non-critical
            pass
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            con = self._get_duckdb_con()
            rows = con.execute(
                f"\n                SELECT\n                    FIRST(title)     AS title,\n                    FIRST(source)    AS source,\n                    url,\n                    ioc,\n                    MAX(confidence) AS confidence,\n                    COUNT(*)         AS hit_count\n                FROM read_parquet('{glob}')\n                WHERE url IS NOT NULL OR ioc IS NOT NULL\n                GROUP BY url, ioc\n                ORDER BY hit_count DESC\n            "
            ).fetchall()
            if rows:
                schema = pa.schema(
                    [
                        ("title", pa.string()),
                        ("source", pa.string()),
                        ("url", pa.string()),
                        ("ioc", pa.string()),
                        ("confidence", pa.float64()),
                        ("hit_count", pa.int64()),
                    ]
                )
                tbl = pa.table.from_pylist(rows, schema=schema)
                pq.write_table(tbl, out, compression="snappy")
            return out
        except Exception:  # noqa: BLE001 — best-effort; export/write failure; non-critical
            return out

    async def _init_dht_node_background(self) -> None:
        """Background init -- DHT node singleton (F214). Fire-and-forget."""
        try:
            import uuid

            from hledac.universal.core.resource_governor import ResourceGovernor
            from hledac.universal.dht.kademlia_node import DHT_BOOTSTRAP_PEERS, DHT_REAL_UDP, KademliaNode

            gov = self._governor or ResourceGovernor()
            node = KademliaNode(
                node_id=f"hledac-sprint-{uuid.uuid4().hex[:8]}", governor=gov, bootstrap_nodes=list(DHT_BOOTSTRAP_PEERS)
            )
            await node.start()
            self._dht_node = node
            log.info("[DHT] singleton started (real_udp=%s)", DHT_REAL_UDP)
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[DHT] background init failed (non-fatal): %s", e)
            self._dht_node = None

    async def _init_i2p_background(self) -> None:
        """Background init -- I2PTransport singleton (F250). Fire-and-forget."""
        try:
            from hledac.universal.transport.i2p_transport import I2PTransport

            transport = I2PTransport()
            await transport.start()
            self._i2p_transport = transport
            log.info("[I2P] background transport started")
            try:
                from hledac.universal.transport.transport_resolver import set_i2p_transport_singleton

                set_i2p_transport_singleton(self._i2p_transport)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
            try:
                from hledac.universal.transport.transport_router import set_i2p_transport_singleton

                set_i2p_transport_singleton(self._i2p_transport)
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                pass
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[I2P] background init failed (non-fatal): %s", e)
            if self._i2p_transport is not None:
                try:
                    self._i2p_transport.available = False
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass

    async def _init_tor_background(self) -> None:
        """Background init -- TorTransport singleton (F214Q). Fire-and-forget."""
        try:
            from hledac.universal.transport.tor_transport import TorTransport, set_tor_transport_singleton

            transport = TorTransport()
            started = await transport.start()
            if not started:
                set_tor_transport_singleton(None)
                log.debug("[Tor] start failed -- transport disabled")
                return
            self._tor_transport = transport
            set_tor_transport_singleton(self._tor_transport)
            log.info("[Tor] background transport started")
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[Tor] background init failed (non-fatal): %s", e)
            if self._tor_transport is not None:
                try:
                    self._tor_transport.available = False
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass

    async def _init_nym_background(self) -> None:
        """Background init -- NymTransport singleton (F250). Fire-and-forget."""
        try:
            from hledac.universal.transport.nym_transport import NYM_CLIENT_AVAILABLE, NymTransport

            if not NYM_CLIENT_AVAILABLE:
                from hledac.universal.transport.nym_transport import set_nym_transport_singleton

                set_nym_transport_singleton(None)
                log.debug("[Nym] nym-client not available -- transport disabled")
                return
            transport = NymTransport()
            await transport.start()
            self._nym_transport = transport
            log.info("[Nym] background transport started")
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[Nym] background init failed (non-fatal): %s", e)
            if self._nym_transport is not None:
                try:
                    self._nym_transport.available = False
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    pass

    async def _memory_pressure_loop(self) -> None:
        """Background task -- adjusts concurrency based on memory pressure."""
        import asyncio as asyncio

        from hledac.universal.resource_allocator import get_recommended_concurrency

        while True:
            try:
                limits = get_recommended_concurrency()
                self._fetch_semaphore = asyncio.Semaphore(limits["fetch"])
                if self._backpressure_monitor is not None:
                    try:
                        bp = await self._backpressure_monitor.evaluate()
                        log.debug(
                            f"[BACKPRESSURE] evaluate: clearnet_max={bp.clearnet_max}, stealth_max={bp.stealth_max}, uma_state={bp.uma_state}"
                        )
                    except Exception as _bp_exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                        log.debug("[BACKPRESSURE] evaluate failed: %s", _bp_exc)
                log.info(f"[MEM] fetch_limit={limits['fetch']} ml_jobs={limits['ml_jobs']}")
                interval = 10 if limits["fetch"] <= 2 else 30
                try:
                    from hledac.universal.monitoring.alert_manager import (
                        check_lock_contention_alert,
                        check_memory_delta_alert,
                        get_lock_contention_tracker,
                    )

                    if _psutil is not None:
                        _current_rss_mb = _psutil.Process().memory_info().rss / (1024 * 1024)
                        await check_memory_delta_alert(self._memory_delta_tracker, _current_rss_mb)
                    await check_lock_contention_alert(get_lock_contention_tracker())
                except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
                    pass
            except Exception as e:  # noqa: BLE001 — best-effort; memory operation; non-critical
                log.warning(f"[MEM] pressure check failed: {e}")
                interval = 30
            _shutdown = getattr(self, "_memory_pressure_shutdown", None)
            if _shutdown is not None and _shutdown.is_set():
                break
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    def consume_shadow_pre_decision(self) -> Any:
        """

        Sprint 8VM: Read-only shadow pre-decision consumer.



        Collects shadow inputs from current scheduler state,

        runs parity check and pre-decision composition,

        and returns PreDecisionSummary.



        Caching: stores result in _shadow_pd_summary to avoid recomputation.

        Cache is cleared in _reset_result().



        THIS IS DIAGNOSTIC ONLY -- all hard boundaries enforced:

        - Does NOT execute any tools (no execute_with_limits calls)

        - Does NOT activate any providers

        - Does NOT write to any ledgers as runtime truth

        - Does NOT modify scheduler mutable state

        - Does NOT create new scheduler framework

        - Does NOT dispatch or enqueue work

        - Returns PreDecisionSummary artifact, NOT a truth store



        Injection point: called from _build_diagnostic_report() at export time.

        The method is also available for ad-hoc calls during sprint for

        diagnostic purposes only.



        Returns None if shadow mode is not active.

        """
        from hledac.universal.runtime.shadow_inputs import RuntimeMode

        if not RuntimeMode.is_shadow_mode():
            return None
        if self._shadow_pd_summary is not None:
            return self._shadow_pd_summary
        lc = None
        if self._lc_adapter is not None:
            lc = self._lc_adapter._lc
        if lc is None:
            return None
        try:
            now_mono = _time.monotonic()
            thermal = "nominal"
            if self._fetch_latency_ema:
                max_ema = max(self._fetch_latency_ema.values()) if self._fetch_latency_ema else 10.0
                if max_ema > 20.0:
                    thermal = "critical"
                elif max_ema > 15.0:
                    thermal = "throttled"
                elif max_ema > 10.0:
                    thermal = "fair"
            lifecycle_bundle = collect_lifecycle_snapshot(
                lc,
                now_mono,
                thermal,
                windup_synthesis_mode="synthesis",
                windup_error=False,
                windup_engine=self._synthesis_engine or "unknown",
            )
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return None
        try:
            graph_bundle = collect_graph_summary(self._ioc_graph)
        except Exception:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
            from hledac.universal.runtime.shadow_inputs import GraphSummaryBundle

            graph_bundle = GraphSummaryBundle()
        try:
            mc_bundle = collect_model_control_facts(
                analyzer_result=None,
                raw_profile={
                    "tools": [],
                    "sources": list(self._config.source_tier_map.keys()),
                    "privacy_level": "STANDARD",
                    "use_tor": False,
                    "depth": "STANDARD",
                    "use_tot": False,
                    "tot_mode": "standard",
                    "models_needed": [],
                },
            )
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            from hledac.universal.runtime.shadow_inputs import ModelControlFactsBundle

            mc_bundle = ModelControlFactsBundle()
        export_facts = {
            "sprint_id": self.sprint_id or "unknown",
            "synthesis_engine": self._synthesis_engine or "unknown",
            "gnn_predictions": 0,
            "top_nodes_count": 0,
            "ranked_parquet_present": False,
            "phase_durations": {},
        }
        try:
            parity = run_shadow_parity(
                lifecycle_bundle=lifecycle_bundle,
                graph_bundle=graph_bundle,
                model_control_bundle=mc_bundle,
                export_handoff_facts=export_facts,
                branch_decision=None,
                provider_recommend=None,
                correlation=None,
                runtime_mode=RuntimeMode.get_current(),
            )
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return None
        try:
            from hledac.universal.brain.model_lifecycle import get_model_lifecycle_status

            lifecycle_status = get_model_lifecycle_status()
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            lifecycle_status = None
        try:
            runtime_facts = collect_provider_runtime_facts(model_manager=None, lifecycle_status=lifecycle_status)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            from hledac.universal.runtime.shadow_inputs import ProviderRuntimeFactsBundle

            runtime_facts = ProviderRuntimeFactsBundle()
        try:
            pd_summary = compose_pre_decision(parity, runtime_facts=runtime_facts)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return None
        try:
            estimated_tool_count = 12
            source_types = list(self._config.source_tier_map.keys())
            has_network_tools = any(
                (
                    s in source_types
                    for s in ["cisa_kev", "threatfox_ioc", "urlhaus_recent", "feodo_ip", "openphish_feed"]
                )
            )
            has_high_memory_tools = False
            pd_summary._tool_readiness_preview = {
                "tool_count": estimated_tool_count,
                "tool_names": [],
                "has_network_tools": has_network_tools,
                "has_high_memory_tools": has_high_memory_tools,
                "tool_cards_sample": [],
                "_deferred_registry": True,
            }
        except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
            pass
        try:
            from hledac.universal.runtime.shadow_pre_decision import preview_dispatch_parity

            task_candidates = [
                "cve_to_github",
                "cve_to_academic",
                "ip_to_ct",
                "ip_to_greynoise",
                "shodan_enrich",
                "domain_to_dns",
                "domain_to_wayback",
                "domain_to_pdns",
                "domain_to_ct",
                "ahmia_search",
                "rdap_lookup",
                "hash_to_mb",
                "wayback_search",
                "commoncrawl_search",
                "paste_keyword_search",
                "github_dork",
                "multi_engine_search",
                "hypothesis_probe",
            ]
            available_caps: set = set()
            if mc_bundle.tools:
                for tool in mc_bundle.tools:
                    if tool in ("web_search", "academic_search"):
                        available_caps.add("reranking")
                    if tool == "entity_extraction":
                        available_caps.add("entity_linking")
            ctrl_mode = lifecycle_bundle.control_phase.mode if hasattr(lifecycle_bundle, "control_phase") else "normal"
            registry_tools: list[str] | None = None
            dispatch_preview = preview_dispatch_parity(
                task_candidates=task_candidates,
                available_capabilities=available_caps,
                control_mode=ctrl_mode,
                registry_tools=registry_tools,
            )
            try:
                from hledac.universal.runtime.shadow_pre_decision import build_execution_context_readiness

                correlation_context: dict[str, Any] | None = None
                if hasattr(self, "_run_id") and self._run_id:
                    correlation_context = {"run_id": self._run_id}
                execlogger_available = hasattr(self, "_tool_execlogger") and self._tool_execlogger is not None
                execution_context = build_execution_context_readiness(
                    dispatch_preview=dispatch_preview,
                    correlation_context=correlation_context,
                    execlogger_available=execlogger_available,
                )
                dispatch_preview.execution_context = execution_context
            except Exception:  # noqa: BLE001 — best-effort; logging failure; non-critical
                pass
            pd_summary.dispatch_parity = dispatch_preview
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        self._shadow_pd_summary = pd_summary
        return pd_summary

    def evaluate_advisory_gate(self) -> None:
        """

        Sprint 8VQ: Evaluate advisory gate at WINDUP entry -- DIAGNOSTIC ONLY.



        Reads from cached PreDecisionSummary (computed by consume_shadow_pre_decision)

        and composes AdvisoryGateSnapshot. Does NOT:

        - Influence dispatch or source ordering

        - Activate providers or tools

        - Write to any ledgers as runtime truth

        - Create new scheduler framework



        Stores ephemeral result in _advisory_gate_snapshot (cleared in _reset_result).

        Output goes into diagnostic report via _build_shadow_readiness_preview().

        """
        from hledac.universal.runtime.shadow_pre_decision import compose_advisory_gate

        pd = self.consume_shadow_pre_decision()
        if pd is None:
            self._advisory_gate_snapshot = None
            return
        try:
            self._advisory_gate_snapshot = compose_advisory_gate(pd)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            self._advisory_gate_snapshot = None

    def _build_shadow_readiness_preview(self) -> dict[str, Any]:
        """

        Sprint 8VM + 8VQ: Build a machine-readable shadow readiness preview dict.



        Called from _build_diagnostic_report() when shadow mode is active.

        This is a READ-ONLY summary extracted from PreDecisionSummary

        for diagnostic/logging purposes -- NOT a truth store.

        """
        pd = self.consume_shadow_pre_decision()
        if pd is None:
            return {}
        result: dict[str, Any] = {
            "runtime_mode": pd.runtime_mode,
            "parity_timestamp_monotonic": pd.parity_timestamp_monotonic,
            "lifecycle_readiness": {
                "phase": pd.lifecycle.workflow_phase,
                "is_active": pd.lifecycle.is_active,
                "is_windup": pd.lifecycle.is_windup,
                "can_accept_work": pd.lifecycle.can_accept_work,
                "should_prune": pd.lifecycle.should_prune,
                "phase_conflict": pd.lifecycle.phase_conflict,
            },
            "graph_readiness": {
                "backend": pd.graph.backend,
                "readiness": pd.graph.readiness,
                "nodes": pd.graph.nodes,
                "edges": pd.graph.edges,
            },
            "export_readiness": {
                "readiness": pd.export_readiness.readiness,
                "synthesis_engine": pd.export_readiness.synthesis_engine,
            },
            "model_control_readiness": {
                "readiness": pd.model_control.readiness,
                "tools_count": pd.model_control.tools_count,
            },
            "diff_taxonomy": [d.name for d in pd.diff_taxonomy],
            "blockers": pd.blockers,
            "unknowns": pd.unknowns,
            "compat_seams": pd.compat_seams,
        }
        if pd.decision_gate is not None:
            result["decision_gate"] = {
                "gate_status": pd.decision_gate.gate_status,
                "blocker_count": pd.decision_gate.blocker_count,
                "unknown_count": pd.decision_gate.unknown_count,
                "compat_seam_count": pd.decision_gate.compat_seam_count,
                "is_proceed_allowed": pd.decision_gate.is_proceed_allowed,
                "defer_to_provider": pd.decision_gate.defer_to_provider,
                "blocker_categories": pd.decision_gate.blocker_categories,
                "unknown_categories": pd.decision_gate.unknown_categories,
            }
        if pd.tool_readiness is not None:
            result["tool_readiness"] = {
                "readiness": pd.tool_readiness.readiness,
                "tool_count": pd.tool_readiness.tool_count,
                "has_network_tools": pd.tool_readiness.has_network_tools,
                "has_high_memory_tools": pd.tool_readiness.has_high_memory_tools,
                "control_mode": pd.tool_readiness.control_mode,
                "pruned_tool_count": pd.tool_readiness.pruned_tool_count,
                "resource_constraint": pd.tool_readiness.resource_constraint,
                "can_execute": pd.tool_readiness.can_execute,
                "defer_reason": pd.tool_readiness.defer_reason,
            }
        if pd.windup_readiness is not None:
            result["windup_readiness"] = {
                "readiness": pd.windup_readiness.readiness,
                "is_windup_phase": pd.windup_readiness.is_windup_phase,
                "synthesis_mode": pd.windup_readiness.synthesis_mode,
                "synthesis_engine": pd.windup_readiness.synthesis_engine,
                "has_export_data": pd.windup_readiness.has_export_data,
                "export_data_quality": pd.windup_readiness.export_data_quality,
                "defer_reason": pd.windup_readiness.defer_reason,
            }
        if pd.provider_note is not None:
            result["provider_activation_note"] = {
                "status": pd.provider_note.status,
                "deferral_reason": pd.provider_note.deferral_reason,
                "has_recommendation": pd.provider_note.has_recommendation,
                "recommendation": pd.provider_note.recommendation,
                "next_phase_hint": pd.provider_note.next_phase_hint,
            }
        if hasattr(pd, "_tool_readiness_preview"):
            result["tool_readiness_preview"] = pd._tool_readiness_preview
        if self._advisory_gate_snapshot is not None:
            ag = self._advisory_gate_snapshot
            result["advisory_gate"] = {
                "gate_outcome": ag.gate_outcome,
                "gate_status": ag.gate_status,
                "blocker_count": ag.blocker_count,
                "unknown_count": ag.unknown_count,
                "compat_seam_count": ag.compat_seam_count,
                "blocker_reasons": ag.blocker_reasons,
                "unknown_reasons": ag.unknown_reasons,
                "compat_seam_reasons": ag.compat_seam_reasons,
                "defer_to_provider": ag.defer_to_provider,
                "gate_evaluated_at_monotonic": ag.gate_evaluated_at_monotonic,
                "gate_evaluated_at_wall": ag.gate_evaluated_at_wall,
            }
        if pd.dispatch_parity is not None:
            result["dispatch_parity"] = {
                "readiness": pd.dispatch_parity.readiness,
                "dispatch_path": pd.dispatch_parity.dispatch_path,
                "canonical_count": pd.dispatch_parity.canonical_count,
                "runtime_only_count": pd.dispatch_parity.runtime_only_count,
                "satisfied_count": pd.dispatch_parity.satisfied_count,
                "blocked_count": pd.dispatch_parity.blocked_count,
                "runtime_only_handlers": pd.dispatch_parity.runtime_only_handlers,
                "blockers": pd.dispatch_parity.blockers,
                "pruned_tools": pd.dispatch_parity.pruned_tools,
                "will_be_pruned": pd.dispatch_parity.will_be_pruned,
                "control_mode": pd.dispatch_parity.control_mode,
            }
            if pd.dispatch_parity.execution_context is not None:
                ec = pd.dispatch_parity.execution_context
                result["execution_context"] = {
                    "capability_ready": ec.capability_ready,
                    "capability_missing": ec.capability_missing,
                    "correlation_ready": ec.correlation_ready,
                    "run_id_present": ec.run_id_present,
                    "branch_id_present": ec.branch_id_present,
                    "provider_id_present": ec.provider_id_present,
                    "action_id_present": ec.action_id_present,
                    "correlation_note": ec.correlation_note,
                    "audit_ready": ec.audit_ready,
                    "execlogger_note": ec.execlogger_note,
                    "canonical_tool_dispatch": ec.canonical_tool_dispatch,
                    "runtime_only_compat_dispatch": ec.runtime_only_compat_dispatch,
                    "blocker_matrix": ec.blocker_matrix,
                }
        if pd.provider_readiness is not None:
            result["provider_readiness"] = {
                "readiness": pd.provider_readiness.readiness,
                "has_recommendation": pd.provider_readiness.has_recommendation,
                "recommendation": pd.provider_readiness.recommendation,
                "lifecycle_ready": pd.provider_readiness.lifecycle_ready,
                "control_ready": pd.provider_readiness.control_ready,
                "thermal_safe": pd.provider_readiness.thermal_safe,
                "has_facts": pd.provider_readiness.has_facts,
                "blockers": pd.provider_readiness.blockers,
                "unknowns": pd.provider_readiness.unknowns,
                "next_phase_hint": pd.provider_readiness.next_phase_hint,
                "deferred_reasons": pd.provider_readiness.deferred_reasons,
            }
        if pd.runtime_facts is not None:
            result["runtime_facts"] = pd.runtime_facts.to_dict()
        return result

    def compute_sprint_intelligence(self) -> dict[str, Any]:
        """

        Sprint 8VN: Lazy fail-soft computation of correlation + hypothesis seams.



        Returns a dict with:

        - correlation: from correlate_findings() -- full second-order condensation

        - hypothesis_pack: from build_hypothesis_pack() -- operator shortlist + actionability

        - branch_value: feed vs public branch value comparison

        - signal_path: dominant signal path, next pivot, corroboration health

        - feed_verdict: aggregated feed economics verdict across cycles

        - public_verdict: aggregated public branch verdict across cycles



        All computation is bounded and M1 8GB safe:

        - correlation: max 500 findings

        - hypothesis: max 200 finding texts

        - feed/public verdict accumulation: max 10 entries each

        - no model dependency

        - fail-soft throughout

        """
        findings = getattr(self, "_all_findings", []) or []
        result: dict[str, Any] = {
            "correlation": None,
            "hypothesis_pack": None,
            "branch_value": None,
            "signal_path": None,
            "feed_verdict": None,
            "public_verdict": None,
        }
        try:
            lane_vlist: list[tuple[str, int, int, int, int]] = getattr(self, "_lane_verdicts", []) or []
            if lane_vlist:
                verdict_tags: dict[str, int] = {}
                total_signal = 0
                total_quality = 0
                for tag, sig, _fb_use, fb_waste, qual in lane_vlist:
                    verdict_tags[tag] = verdict_tags.get(tag, 0) + sig
                    total_signal += sig
                    total_quality += qual
                if self._public_outcome is not None:
                    pub_accepted = self._public_outcome.get("accepted_count", 0) or 0
                    if pub_accepted > 0:
                        verdict_tags["public"] = verdict_tags.get("public", 0) + pub_accepted
                        total_signal += pub_accepted
                dominant_tag = max(verdict_tags, key=verdict_tags.get) if verdict_tags else "none"
                avg_quality = total_quality / len(lane_vlist) if lane_vlist else 0.0
                result["lane_verdict"] = {
                    "dominant_tag": dominant_tag,
                    "ct_storage_rejection_reasons": list(getattr(self._result, "ct_storage_rejection_reasons", ())),
                    "cycle_count": len(lane_vlist),
                    "total_signal_strength": total_signal,
                    "tag_distribution": verdict_tags,
                    "avg_quality": avg_quality,
                    "ct_findings": self._result.lane_ct_accepted_findings,
                    "wayback_findings": self._result.lane_wayback_accepted_findings,
                    "pdns_findings": self._result.lane_pdns_accepted_findings,
                    "blockchain_findings": self._result.lane_blockchain_accepted_findings,
                    "ipfs_findings": self._result.lane_ipfs_accepted_findings,
                    "doh_findings": self._result.lane_doh_accepted_findings,
                    "public_findings": self._result.lane_public_accepted_findings,
                    "ct_loss_stage": getattr(self._result, "ct_loss_stage", "no_loss"),
                    "ct_bridge_invoked": getattr(self._result, "ct_bridge_invoked", False),
                    "ct_raw_count": getattr(self._result, "ct_raw_count", 0),
                    "ct_raw_sample_count": getattr(self._result, "ct_raw_sample_count", 0),
                    "ct_candidates_built": getattr(self._result, "ct_candidates_built", 0),
                    "ct_bridge_rejections_count": getattr(self._result, "ct_bridge_rejections_count", 0),
                    "ct_candidates_accumulated": getattr(self._result, "ct_candidates_accumulated", 0),
                    "ct_candidates_stored": getattr(self._result, "ct_candidates_stored", 0),
                    "ct_storage_rejected": getattr(self._result, "ct_storage_rejected", 0),
                    "ct_bridge_candidate_count": getattr(self._result, "ct_candidate_count", 0),
                    "ct_bridge_valid_domain_count": getattr(self._result, "ct_valid_domain_count", 0),
                    "ct_bridge_build_success_count": getattr(self._result, "ct_bridge_build_success_count", 0),
                    "ct_bridge_quality_rejected_count": getattr(self._result, "ct_bridge_quality_rejected_count", 0),
                    "ct_expansion_clues_count": getattr(self._result, "ct_expansion_clues_count", 0),
                    "ct_valid_public_domains": getattr(self._result, "ct_valid_public_domains", 0),
                    "ct_wildcard_domains": getattr(self._result, "ct_wildcard_domains", 0),
                    "ct_private_reserved_domains": getattr(self._result, "ct_private_reserved_domains", 0),
                    "ct_duplicate_candidates": getattr(self._result, "ct_duplicate_candidates", 0),
                    "quality_rejection_summary_by_family": getattr(
                        self._result, "quality_rejection_summary_by_family", {}
                    ),
                    "duplicate_rejection_summary_by_family": getattr(
                        self._result, "duplicate_rejection_summary_by_family", {}
                    ),
                    "low_information_by_family": getattr(self._result, "low_information_by_family", {}),
                    "quality_rejection_ledger_size": len(getattr(self._result, "quality_rejection_ledger", ())),
                    "wayback_advisory_clues_count": getattr(self._result, "wayback_advisory_clues_count", 0),
                    "wayback_changed_url_count": getattr(self._result, "wayback_changed_url_count", 0),
                    "wayback_added_url_count": getattr(self._result, "wayback_added_url_count", 0),
                    "wayback_digest_changed_count": getattr(self._result, "wayback_digest_changed_count", 0),
                    "wayback_unchanged_rejected": getattr(self._result, "wayback_unchanged_rejected", 0),
                    "passive_dns_advisory_clues_count": getattr(self._result, "passive_dns_advisory_clues_count", 0),
                    "passive_dns_private_ip_rejected": getattr(self._result, "passive_dns_private_ip_rejected", 0),
                    "passive_dns_empty_ip_rejected": getattr(self._result, "passive_dns_empty_ip_rejected", 0),
                    "arrow_last_flush_error": getattr(self._result, "arrow_last_flush_error", "") or "",
                    "arrow_batch_dropped": getattr(self._result, "arrow_batch_dropped_after_flush_failure", 0),
                    "prewindup_barrier_errors": getattr(self._result, "prewindup_barrier_errors", 0) or 0,
                    "return_guard_errors": getattr(self._result, "return_guard_errors", 0) or 0,
                }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            result["lane_verdict"] = None
        if not findings:
            return result
        try:
            correlate_fn = _import_correlate_findings()
            corr = correlate_fn(findings[:500])
            result["correlation"] = {
                "risk_score": round(corr.risk_score, 3),
                "verdict": corr.verdict,
                "anomaly_count": corr.anomaly_count,
                "top_themes": list(corr.top_themes[:5]),
                "theme_count": len(corr.themes),
                "signal_quality": getattr(corr, "signal_quality", "weak"),
                "cross_source_confidence": round(getattr(corr, "cross_source_confidence", 0.0), 3),
                "campaign_confidence": round(getattr(corr, "campaign_confidence", 0.0), 3),
                "dominant_cluster": getattr(corr, "dominant_cluster", None),
                "so_what": getattr(corr, "so_what", ""),
                "what_matters_first": getattr(corr, "what_matters_first", ""),
                "operator_shortlist": [
                    {
                        "action": item.get("action", ""),
                        "target": item.get("target", "")[:80],
                        "rationale": item.get("rationale", ""),
                    }
                    for item in (getattr(corr, "operator_shortlist", None) or [])[:3]
                    if isinstance(item, dict)
                ],
                "confidence_note": getattr(corr, "confidence_note", ""),
                "corroborated_iocs_count": len(getattr(corr, "corroborated_iocs", []) or []),
                "top_priority_pivots_count": len(getattr(corr, "top_priority_pivots", []) or []),
            }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            result["correlation"] = None
        try:
            HypEng = _import_hypothesis_engine()
            eng = HypEng()
            finding_texts: list[str] = []
            for f in findings[:200]:
                desc = f.get("description", "")
                src = f.get("source", "")
                finding_texts.append(f"[{src}] {desc}" if src and desc else desc or "")
            if finding_texts:
                pack = eng.build_hypothesis_pack(finding_texts)
                result["hypothesis_pack"] = {
                    "hypothesis_count": len(pack.hypotheses),
                    "query_count": len(pack.suggested_queries),
                    "ioc_follow_ups": len(pack.ioc_follow_ups),
                    "source_hints_count": len(pack.source_hints),
                    "provenance": pack.provenance,
                    "signal_quality": getattr(pack, "signal_quality", "weak"),
                    "what_matters_first": getattr(pack, "what_matters_first", ""),
                    "confidence_note": getattr(pack, "confidence_note", ""),
                    "top_queries": [
                        {"query": q.get("query", ""), "rationale": q.get("rationale", "")[:80]}
                        for q in (pack.suggested_queries or [])[:5]
                        if isinstance(q, dict)
                    ],
                    "operator_shortlist": [
                        {
                            "action": item.get("action", ""),
                            "target": item.get("target", "")[:80],
                            "rationale": item.get("rationale", ""),
                        }
                        for item in (getattr(pack, "operator_shortlist", None) or [])[:3]
                        if isinstance(item, dict)
                    ],
                    "discarded_as_redundant": [
                        {
                            "action_type": item.get("action_type", ""),
                            "query": item.get("query", "")[:120],
                            "reason_discarded": item.get("reason_discarded", ""),
                            "pivot_type": item.get("pivot_type", ""),
                            "priority": item.get("priority", 0.0),
                        }
                        for item in (
                            getattr(pack, "discarded_as_redundant", lambda max_items=3: [])(max_items=3) or []
                        )[:3]
                        if isinstance(item, dict)
                    ],
                }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            result["hypothesis_pack"] = None
        try:
            feed_vlist: list[tuple[str, int, int, int, int]] = getattr(self, "_feed_verdicts", []) or []
            if feed_vlist:
                verdict_tags: dict[str, int] = {}
                total_signal = 0
                total_fallback_waste = 0
                for tag, sig, _fb_use, fb_waste, qual in feed_vlist:
                    verdict_tags[tag] = verdict_tags.get(tag, 0) + 1
                    total_signal += sig
                    total_fallback_waste += fb_waste
                dominant_tag = max(verdict_tags, key=verdict_tags.get) if verdict_tags else ""
                avg_quality = round(sum((v[4] for v in feed_vlist)) / len(feed_vlist), 2) if feed_vlist else 0.0
                result["feed_verdict"] = {
                    "dominant_tag": dominant_tag,
                    "cycle_count": len(feed_vlist),
                    "total_signal_strength": total_signal,
                    "total_fallback_waste": total_fallback_waste,
                    "avg_quality": avg_quality,
                    "tag_distribution": verdict_tags,
                }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            result["feed_verdict"] = None
        try:
            pub_vlist: list[dict] = getattr(self, "_public_verdicts", []) or []
            if pub_vlist:
                waste_ratios = [v.get("waste_ratio", 0.0) for v in pub_vlist if "waste_ratio" in v]
                value_ratios = [v.get("value_ratio", 0.0) for v in pub_vlist if "value_ratio" in v]
                corroborations = [
                    v.get("corroboration_vs_burn", 0.0) for v in pub_vlist if "corroboration_vs_burn" in v
                ]
                next_actions = [v.get("public_next_action", "") for v in pub_vlist if "public_next_action" in v]
                confidence_notes = [
                    v.get("public_confidence_note", "") for v in pub_vlist if "public_confidence_note" in v
                ]
                squandered_hits = [v.get("discovery_squandered", 0) for v in pub_vlist if "discovery_squandered" in v]
                noise_ratios = [v.get("noise_fetch_ratio", 0.0) for v in pub_vlist if "noise_fetch_ratio" in v]
                dominant_action = max(set(next_actions), key=next_actions.count) if next_actions else ""
                dominant_conf = max(set(confidence_notes), key=confidence_notes.count) if confidence_notes else ""
                result["public_verdict"] = {
                    "cycle_count": len(pub_vlist),
                    "avg_waste_ratio": round(sum(waste_ratios) / len(waste_ratios), 3) if waste_ratios else 0.0,
                    "avg_value_ratio": round(sum(value_ratios) / len(value_ratios), 3) if value_ratios else 0.0,
                    "avg_corroboration_vs_burn": round(sum(corroborations) / len(corroborations), 3)
                    if corroborations
                    else 0.0,
                    "avg_discovery_squandered": round(sum(squandered_hits) / len(squandered_hits), 2)
                    if squandered_hits
                    else 0.0,
                    "total_discovery_squandered": sum(squandered_hits),
                    "avg_noise_fetch_ratio": round(sum(noise_ratios) / len(noise_ratios), 3) if noise_ratios else 0.0,
                    "dominant_next_action": dominant_action,
                    "dominant_confidence_note": dominant_conf,
                    "action_distribution": {a: next_actions.count(a) for a in set(next_actions)},
                }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            result["public_verdict"] = None
        try:
            lane_vlist: list[tuple[str, int, int, int, int]] = getattr(self, "_lane_verdicts", []) or []
            if lane_vlist:
                verdict_tags: dict[str, int] = {}
                total_signal = 0
                total_quality = 0
                for tag, sig, _fb_use, fb_waste, qual in lane_vlist:
                    verdict_tags[tag] = verdict_tags.get(tag, 0) + sig
                    total_signal += sig
                    total_quality += qual
                dominant_tag = max(verdict_tags, key=verdict_tags.get) if verdict_tags else "none"
                avg_quality = total_quality / len(lane_vlist) if lane_vlist else 0.0
                result["lane_verdict"] = {
                    "dominant_tag": dominant_tag,
                    "ct_storage_rejection_reasons": list(getattr(self._result, "ct_storage_rejection_reasons", ())),
                    "cycle_count": len(lane_vlist),
                    "total_signal_strength": total_signal,
                    "tag_distribution": verdict_tags,
                    "avg_quality": avg_quality,
                    "ct_findings": self._result.lane_ct_accepted_findings,
                    "wayback_findings": self._result.lane_wayback_accepted_findings,
                    "pdns_findings": self._result.lane_pdns_accepted_findings,
                    "blockchain_findings": self._result.lane_blockchain_accepted_findings,
                    "ipfs_findings": self._result.lane_ipfs_accepted_findings,
                    "doh_findings": self._result.lane_doh_accepted_findings,
                    "ct_loss_stage": getattr(self._result, "ct_loss_stage", "no_loss"),
                    "ct_bridge_invoked": getattr(self._result, "ct_bridge_invoked", False),
                    "ct_raw_count": getattr(self._result, "ct_raw_count", 0),
                    "ct_raw_sample_count": getattr(self._result, "ct_raw_sample_count", 0),
                    "ct_candidates_built": getattr(self._result, "ct_candidates_built", 0),
                    "ct_bridge_rejections_count": getattr(self._result, "ct_bridge_rejections_count", 0),
                    "ct_candidates_accumulated": getattr(self._result, "ct_candidates_accumulated", 0),
                    "ct_candidates_stored": getattr(self._result, "ct_candidates_stored", 0),
                    "ct_storage_rejected": getattr(self._result, "ct_storage_rejected", 0),
                    "ct_bridge_candidate_count": getattr(self._result, "ct_candidate_count", 0),
                    "ct_bridge_valid_domain_count": getattr(self._result, "ct_valid_domain_count", 0),
                    "ct_bridge_build_success_count": getattr(self._result, "ct_bridge_build_success_count", 0),
                    "ct_bridge_quality_rejected_count": getattr(self._result, "ct_bridge_quality_rejected_count", 0),
                    "ct_expansion_clues_count": getattr(self._result, "ct_expansion_clues_count", 0),
                    "ct_valid_public_domains": getattr(self._result, "ct_valid_public_domains", 0),
                    "ct_wildcard_domains": getattr(self._result, "ct_wildcard_domains", 0),
                    "ct_private_reserved_domains": getattr(self._result, "ct_private_reserved_domains", 0),
                    "ct_duplicate_candidates": getattr(self._result, "ct_duplicate_candidates", 0),
                    "wayback_advisory_clues_count": getattr(self._result, "wayback_advisory_clues_count", 0),
                    "wayback_changed_url_count": getattr(self._result, "wayback_changed_url_count", 0),
                    "wayback_added_url_count": getattr(self._result, "wayback_added_url_count", 0),
                    "wayback_digest_changed_count": getattr(self._result, "wayback_digest_changed_count", 0),
                    "wayback_unchanged_rejected": getattr(self._result, "wayback_unchanged_rejected", 0),
                    "passive_dns_advisory_clues_count": getattr(self._result, "passive_dns_advisory_clues_count", 0),
                    "passive_dns_private_ip_rejected": getattr(self._result, "passive_dns_private_ip_rejected", 0),
                    "passive_dns_empty_ip_rejected": getattr(self._result, "passive_dns_empty_ip_rejected", 0),
                    "arrow_last_flush_error": getattr(self._result, "arrow_last_flush_error", "") or "",
                    "arrow_batch_dropped": getattr(self._result, "arrow_batch_dropped_after_flush_failure", 0),
                    "prewindup_barrier_errors": getattr(self._result, "prewindup_barrier_errors", 0) or 0,
                    "return_guard_errors": getattr(self._result, "return_guard_errors", 0) or 0,
                }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            result["lane_verdict"] = None
        try:
            corr = result.get("correlation") or {}
            sig_quality = corr.get("signal_quality", "weak")
            cross_conf = corr.get("cross_source_confidence", 0.0)
            camp_conf = corr.get("campaign_confidence", 0.0)
            feed_f = self._result.accepted_findings or 0
            pub_f = self._result.public_accepted_findings or 0
            lane_f = (
                self._result.lane_ct_accepted_findings
                + self._result.lane_wayback_accepted_findings
                + self._result.lane_pdns_accepted_findings
                + self._result.lane_blockchain_accepted_findings
                + self._result.lane_ipfs_accepted_findings
                + self._result.lane_doh_accepted_findings
            )
            total_findings = feed_f + pub_f + lane_f
            if sig_quality == "strong":
                dominant_path = "corroborated" if cross_conf > 0.5 else "high_confidence"
            elif sig_quality == "mixed":
                dominant_path = "multi_source" if cross_conf > 0.3 else "degraded"
            else:
                dominant_path = "weak_noisy"
            top_pivots_count = corr.get("top_priority_pivots_count", 0)
            next_pivot = "pivot_immediately" if top_pivots_count > 0 and sig_quality != "weak" else "hold_pivoting"
            corroboration_score = round(cross_conf * 0.6 + camp_conf * 0.4, 3)
            match (total_findings, feed_f, pub_f, sig_quality):
                case [0, _, _, _]:
                    branch_mix_health = "empty"
                case [_, 0, 0, _]:
                    branch_mix_health = "empty"
                case [_, 0, pub, _]:
                    branch_mix_health = "public_only" if pub > 3 else "public_sparse"
                case [_, feed, 0, _]:
                    branch_mix_health = "feed_only" if feed > 3 else "feed_sparse"
                case [_, feed, pub, sq] if feed / pub > 5:
                    branch_mix_health = "feed_heavy"
                case [_, feed, pub, sq] if feed / pub < 0.2:
                    branch_mix_health = "public_heavy"
                case [_, _, _, "strong"]:
                    branch_mix_health = "healthy_balanced"
                case _:
                    branch_mix_health = "balanced_low_yield"
            feed_dominance_override = (
                self._result.feed_dominance_ratio is not None
                and self._result.feed_dominance_ratio >= 0.95
                and ((self._result.public_accepted_findings or 0) == 0)
                and (
                    self._result.lane_ct_accepted_findings
                    + self._result.lane_wayback_accepted_findings
                    + self._result.lane_pdns_accepted_findings
                    + self._result.lane_blockchain_accepted_findings
                    + self._result.lane_ipfs_accepted_findings
                    + self._result.lane_doh_accepted_findings
                    == 0
                )
            )
            if feed_dominance_override:
                dominant_path = "feed"
                is_corroborated_flag = False
            else:
                is_corroborated_flag = cross_conf > 0.4
            result["signal_path"] = {
                "dominant_signal_path": dominant_path,
                "next_pivot_recommendation": next_pivot,
                "corroboration_score": corroboration_score,
                "branch_mix_health": branch_mix_health,
                "is_noisy": sig_quality == "weak" and cross_conf < 0.2,
                "is_corroborated": is_corroborated_flag,
                "campaign_signal": camp_conf > 0.3,
            }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            result["signal_path"] = None
        try:
            feed_f = self._result.accepted_findings or 0
            pub_f = self._result.public_accepted_findings or 0
            feed_h = self._result.total_pattern_hits or 0
            pub_h = self._result.public_matched_patterns or 0
            total = feed_f + pub_f
            if total > 0:
                feed_pct = round(feed_f / total * 100, 1)
                pub_pct = round(pub_f / total * 100, 1)
            else:
                feed_pct = pub_pct = 0.0
            if pub_f > feed_f * 1.5:
                branch_verdict = "public_dominant"
                recommendation = "expand_public_branch"
            elif feed_f > pub_f * 1.5:
                branch_verdict = "feed_dominant"
                recommendation = "expand_feed_branch"
            else:
                branch_verdict = "balanced"
                recommendation = "maintain_both"
            result["branch_value"] = {
                "feed_findings": feed_f,
                "public_findings": pub_f,
                "feed_pattern_hits": feed_h,
                "public_pattern_hits": pub_h,
                "feed_pct": feed_pct,
                "public_pct": pub_pct,
                "branch_verdict": branch_verdict,
                "recommendation": recommendation,
            }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            result["branch_value"] = None
        try:
            sig_path = result.get("signal_path") or {}
            br_val = result.get("branch_value") or {}
            feed_v = result.get("feed_verdict") or {}
            pub_v = result.get("public_verdict") or {}
            corr = result.get("correlation") or {}
            hyp = result.get("hypothesis_pack") or {}
            dominant_signal = sig_path.get("dominant_signal_path", "unknown")
            branch_verdict = br_val.get("branch_verdict", "unknown")
            corroboration_score = sig_path.get("corroboration_score", 0.0)
            is_noisy = sig_path.get("is_noisy", False)
            is_corroborated = sig_path.get("is_corroborated", False)
            campaign_signal = sig_path.get("campaign_signal", False)
            branch_mix = sig_path.get("branch_mix_health", "unknown")
            next_pivot = sig_path.get("next_pivot_recommendation", "unknown")
            avg_corr_vs_burn = pub_v.get("avg_corroboration_vs_burn", 0.0)
            avg_noise = pub_v.get("avg_noise_fetch_ratio", 0.0)
            dominant_action = pub_v.get("dominant_next_action", "")
            dominant_conf = pub_v.get("dominant_confidence_note", "")
            feed_tag = feed_v.get("dominant_tag", "")
            feed_avg_qual = feed_v.get("avg_quality", 0.0)
            risk_score = corr.get("risk_score", 0.0)
            hyp_count = hyp.get("hypothesis_count", 0)
            what_matters = corr.get("what_matters_first") or hyp.get("what_matters_first") or ""
            op_shortlist = corr.get("operator_shortlist", []) or hyp.get("operator_shortlist", [])
            first_action = op_shortlist[0].get("action", "") if op_shortlist else ""
            backup_action = op_shortlist[1].get("action", "") if len(op_shortlist) > 1 else ""
            total_findings = (br_val.get("feed_findings", 0) or 0) + (br_val.get("public_findings", 0) or 0)
            if total_findings == 0:
                posture = "depleted"
            elif (
                self._result.feed_dominance_ratio is not None
                and self._result.feed_dominance_ratio >= 0.95
                and ((br_val.get("public_findings", 0) or 0) == 0)
                and ((br_val.get("feed_findings", 0) or 0) > 0)
            ):
                posture = "noisy"
            elif is_noisy and avg_noise > 0.4:
                posture = "noisy"
            elif is_corroborated and corroboration_score > 0.35:
                posture = "corroborated"
            elif campaign_signal and avg_corr_vs_burn > 0.35:
                posture = "mixed"
            elif dominant_signal in ("corroborated", "high_confidence"):
                posture = "corroborated"
            elif dominant_signal == "weak_noisy":
                posture = "noisy"
            else:
                posture = "mixed"
            export_ready = total_findings > 0 and bool(corr or hyp)
            if is_corroborated and corroboration_score > 0.4 and (avg_noise < 0.3):
                proof_grade = "strong"
            elif is_corroborated and corroboration_score > 0.25:
                proof_grade = "moderate"
            elif total_findings > 0:
                proof_grade = "weak"
            else:
                proof_grade = "none"
            operator_ready = bool(op_shortlist and first_action)
            decision_pressure = "high" if posture in ("noisy", "mixed") and total_findings > 0 else "low"
            avg_noise = pub_v.get("avg_noise_fetch_ratio", 0.0)
            branch_conversion_health = round(
                (1.0 if is_corroborated else 0.0) * corroboration_score * (1.0 - avg_noise), 3
            )
            total_squandered = pub_v.get("total_discovery_squandered", 0) or 0
            discovery_efficiency = round(total_findings / (1 + total_squandered), 3) if total_findings > 0 else 0.0
            result["sprint_verdict"] = {
                "posture": posture,
                "dominant_signal": dominant_signal,
                "branch_verdict": branch_verdict,
                "branch_mix": branch_mix,
                "corroboration_score": corroboration_score,
                "is_corroborated": is_corroborated,
                "campaign_signal": campaign_signal,
                "next_pivot": next_pivot,
                "dominant_action": dominant_action,
                "first_action": first_action,
                "backup_action": backup_action,
                "intel_what_matters": what_matters,
                "confidence": dominant_conf,
                "feed_tag": feed_tag,
                "feed_avg_quality": feed_avg_qual,
                "risk_score": risk_score,
                "hypothesis_count": hyp_count,
                "total_findings": total_findings,
                "export_ready": export_ready,
                "proof_grade": proof_grade,
                "operator_ready": operator_ready,
                "decision_pressure": decision_pressure,
                "branch_conversion_health": branch_conversion_health,
                "discovery_efficiency": discovery_efficiency,
            }
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        return result

    def _reset_result(self) -> None:
        # ISSUE-3: BoundedLRUDict.clear() resets content but preserves capacity.
        # Reset eviction counters for fresh telemetry in next sprint.
        self._seen_hashes.clear()
        self._seen_hashes.reset_evicted_count()
        self._entries_per_source.clear()
        self._entries_per_source.reset_evicted_count()
        self._hits_per_source.clear()
        self._hits_per_source.reset_evicted_count()
        self._stop_requested = False
        self._result = SprintSchedulerResult()
        # ISSUE-3: wire BoundedLRUDict eviction counters into result for telemetry
        self._result.seen_hashes_dropped = self._seen_hashes.evicted_count
        self._result.entries_per_source_dropped = self._entries_per_source.evicted_count
        self._result.hits_per_source_dropped = self._hits_per_source.evicted_count
        ctx = get_sprint_ctx()
        # ctx-level bounded dicts: wire their eviction counters into result too
        self._result.seen_hashes_dropped += ctx.seen_hashes.evicted_count
        self._result.entries_per_source_dropped += ctx.entries_per_source.evicted_count
        self._result.hits_per_source_dropped += ctx.hits_per_source.evicted_count
        self._result.novelty_bonuses_dropped = ctx.novelty_bonuses.evicted_count
        self._result.source_weights_dropped = ctx.source_weights.evicted_count
        self._result.feed_accepted_per_source_dropped = ctx.feed_accepted_per_source.evicted_count
        self._result.fetch_latency_ema_dropped = ctx.fetch_latency_ema.evicted_count
        get_sprint_ctx().arrow_batch.clear()
        # ISSUE-6: also clear SprintRunContext fields that accumulate unbounded.
        # pivot_rewards is NOT cleared — history[-20:] already bounds reads, and
        # keeping it across cycles is intentional for hypothesis tracking.
        ctx = get_sprint_ctx()
        ctx.seen_hashes.clear()
        ctx.seen_hashes.reset_evicted_count()
        ctx.novelty_bonuses.clear()
        ctx.novelty_bonuses.reset_evicted_count()
        ctx.source_weights.clear()
        ctx.source_weights.reset_evicted_count()
        ctx.feed_accepted_per_source.clear()
        ctx.feed_accepted_per_source.reset_evicted_count()
        ctx.fetch_latency_ema.clear()
        ctx.fetch_latency_ema.reset_evicted_count()
        ctx.entries_per_source.clear()
        ctx.entries_per_source.reset_evicted_count()
        ctx.hits_per_source.clear()
        ctx.hits_per_source.reset_evicted_count()
        self._arrow_last_flush = 0.0
        if self._duckdb_read_con is not None:
            try:
                self._duckdb_read_con.close()
            except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                pass
            self._duckdb_read_con = None
        self._shadow_pd_summary = None
        self._advisory_gate_snapshot = None
        self._source_quality_feedback.clear()
        if self._prefetch_oracle is not None:
            try:
                self._prefetch_oracle.reset()
            except Exception:  # noqa: BLE001 — best-effort; prefetch/oracle failure; non-critical
                pass
        self._all_findings.clear()
        if self._duckdb_store is not None:
            try:
                _quality_state = getattr(self._duckdb_store, "_quality_state", None)
                if _quality_state is not None and hasattr(_quality_state, "reset_hot_cache"):
                    _quality_state.reset_hot_cache()
            except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                pass
        self._synth_windup_task = None
        self._feed_accepted_per_source.clear()
        self._feed_budget_triggered = False
        self._correlation_cache = None
        self._hypothesis_pack_cache = None
        self._branch_value_summary = None
        self._feed_verdicts.clear()
        self._public_verdicts.clear()
        self._public_outcome = None
        self._public_pipeline_result = None
        self._query = ""
        self._nonfeed_predispatch_done = False
        self._prewindup_barrier_delayed = False
        self._barrier_retry_count = 0
        self._source_economics.clear()
        try:
            from hledac.universal.knowledge.evidence_chain import reset_global_builder

            reset_global_builder()
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass
        try:
            from hledac.universal.knowledge.graph_service import reset_session

            reset_session()
        except Exception:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
            pass
        if self._hermes_engine is not None:
            try:
                self._hermes_engine.reset_session()
            except Exception:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
                pass
        self._governor = None
        self._doh_adapter = None
        if hasattr(self, "_sidecar_orchestrator") and self._sidecar_orchestrator is not None:
            self._sidecar_orchestrator.reset()
        self._lane_rejections = []
        self._lane_rejections_total_seen = 0
        self._lane_rejections_dropped = 0
        self._result.acquisition_lane_outcomes = ()
        self._lane_verdicts.clear()
        self._result.lane_ct_accepted_findings = 0
        self._result.lane_wayback_accepted_findings = 0
        self._result.lane_pdns_accepted_findings = 0
        self._result.lane_blockchain_accepted_findings = 0
        self._result.lane_ipfs_accepted_findings = 0
        self._result.lane_doh_accepted_findings = 0
        self._result.wayback_attempted = False
        self._result.wayback_raw_count = 0
        self._result.wayback_candidates_built = 0
        self._result.wayback_accepted_count = 0
        self._result.passive_dns_attempted = False
        self._result.passive_dns_raw_count = 0
        self._result.passive_dns_candidates_built = 0
        self._result.passive_dns_accepted_count = 0
        self._result.doh_planned = False
        self._result.doh_scheduled = False
        self._result.doh_request_attempted = False
        self._result.doh_domains_attempted = 0
        self._result.doh_raw_count = 0
        self._result.doh_accepted_findings = 0
        self._result.doh_terminal_stage = ""
        self._result.doh_provider_errors = ()
        self._result.doh_cache_used = False
        self._result.doh_seed_source = ""
        self._result.source_family_events.clear()

    def _emit_source_family_event(
        self, family: str, event: str, count: int = 0, reason: str = "", terminal_state: str = ""
    ) -> None:
        """

        Emit a bounded source-family lifecycle event for diagnostics.



        Caps at MAX_SOURCE_FAMILY_EVENTS (200), dropping oldest when full.

        Event dict contains: family, event, count, reason, terminal_state, ts_monotonic

        """
        evt = {
            "family": family,
            "event": event,
            "count": count,
            "reason": reason,
            "terminal_state": terminal_state,
            "ts_monotonic": _time.monotonic(),
        }
        events = self._result.source_family_events
        if len(events) >= self._result.MAX_SOURCE_FAMILY_EVENTS:
            events.pop(0)
        events.append(evt)

    async def _run_graph_rag_context_sidecar(self, query: str, duckdb_store: Any) -> list[Any]:
        """
        Sprint F224: Graph RAG pre-cycle enrichment.

        Runs BEFORE first cycle to inject previously discovered graph context
        into the sprint. Uses multi-hop search over DuckPGQGraph to find
        relevant entities/relationships from previous sprints.

        Gate: HLEDAC_ENABLE_GRAPH_RAG=1 + RAM check < 5.0GB

        Args:
            query: Current sprint query
            duckdb_store: DuckDB store for persistent state

        Returns:
            List of CanonicalFinding with "context_seed" source_type
        """
        findings: list[Any] = []
        try:
            try:
                from hledac.universal.utils.uma_budget import get_uma_snapshot

                uma = get_uma_snapshot()
                if uma.is_critical or uma.is_emergency:
                    logger.debug("[graph_rag] skipped -- memory pressure")
                    return []
            except Exception:  # noqa: BLE001 — best-effort; memory operation; non-critical
                pass
            try:
                from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
                from hledac.universal.knowledge.graph_service import GraphService
            except ImportError as _e:
                logger.debug("[graph_rag] import failed: %s", _e)
                return []
            graph_service = GraphService()
            orchestrator = GraphRAGOrchestrator(graph_service)
            result = await orchestrator.multi_hop_search(query=query, hops=2, max_nodes=20)
            insights = result.get("insights", [])
            if not insights:
                return []
            CanonicalFinding = None
            try:
                CanonicalFinding = CF
            except ImportError:
                pass
            ts = _time.time()
            for idx, insight in enumerate(insights[:20]):
                content = insight.get("content", "")
                if not content:
                    continue
                if CanonicalFinding:
                    finding = CanonicalFinding(
                        finding_id=f"graph_rag_ctx_{int(ts * 1000)}_{idx}",
                        query=query,
                        source_type=SourceType.CONTEXT_SEED,
                    )
                    if self._evidence_log is not None:
                        try:
                            self._evidence_log.create_event(
                                "observation",
                                finding.model_dump() if hasattr(finding, "model_dump") else vars(finding),
                                source_ids=[finding.source_id] if hasattr(finding, "source_id") else [],
                                confidence=insight.get("similarity", 0.5),
                                ts=ts,
                                provenance=("graph_rag",),
                                payload_text=content[:2048],
                            )
                        except Exception:  # noqa: BLE001 — best-effort; graph operation failure; non-critical
                            pass
                    findings.append(finding)
                else:
                    findings.append(
                        {
                            "finding_id": f"graph_rag_ctx_{int(ts * 1000)}_{idx}",
                            "query": query,
                            "source_type": "context_seed",
                            "confidence": insight.get("similarity", 0.5),
                            "ts": ts,
                            "provenance": ("graph_rag",),
                            "payload_text": content[:2048],
                        }
                    )
            logger.debug("[graph_rag] found %d context insights", len(findings))
        except Exception as _e:  # noqa: BLE001 — best-effort; logging failure; non-critical
            logger.debug("[graph_rag] sidecar failed: %s", _e)
        return findings


async def async_run_tiered_feed_sprint_once(
    sources: Sequence[str],
    config: SprintSchedulerConfig | None = None,
    lifecycle: object | None = None,
    now_monotonic: float | None = None,
    query: str = "",
    duckdb_store: Any = None,
) -> SprintSchedulerResult:
    """

    One-shot tiered feed sprint.



    Creates its own lifecycle if none provided.

    """
    if config is None:
        config = SprintSchedulerConfig()
    if lifecycle is None:
        from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager

        lifecycle = SprintLifecycleManager(
            sprint_duration_s=config.sprint_duration_s, windup_lead_s=config.effective_windup_lead_s
        )
    scheduler = SprintScheduler(config)
    return await scheduler.run(lifecycle, sources, now_monotonic, query, duckdb_store)


SPRINT_TIERS: dict = {
    "quick": {"min_duration": 60, "hermes": False, "windup_lead_s": 0},
    "standard": {"min_duration": 180, "hermes": True, "windup_lead_s": 30},
    "deep": {"min_duration": 300, "hermes": True, "windup_lead_s": 30},
    "thorough": {"min_duration": 600, "hermes": True, "windup_lead_s": 30},
}


class SprintTooShortError(ValueError):
    """Raised when sprint duration is below minimum."""

    pass


def detect_sprint_tier(duration_s: float) -> str:
    """Detect sprint tier from duration in seconds."""
    if duration_s < 60:
        raise SprintTooShortError(f"Sprint duration {duration_s}s is below minimum 60s")
    if duration_s < 180:
        return "quick"
    if duration_s < 300:
        return "standard"
    if duration_s < 600:
        return "deep"
    return "thorough"
