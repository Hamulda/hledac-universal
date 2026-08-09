"""
F186A CANONICAL SPRINT TRUTH CLOSURE — CLI Entry Point: python -m hledac.universal.runtime.sprint_entrypoint

Pre-sprint checks, UMA wiring, sprint_delta reporting.









Wires UMAAlarmDispatcher → SprintScheduler wind-down callbacks.

================================================================
F186A CANONICAL SPRINT TRUTH — ROLE TABLE
================================================================
Role        | Function                        | Owner | Notes
----------- | ------------------------------- | ----- | ----
canonical   | run_sprint()                    | YES   | SOLE canonical sprint owner
canonical   | _runtime_truth()                | YES   | part of canonical run boundary
canonical   | _is_meaningful_run()            | YES   | part of canonical run boundary
canonical   | run_pre_sprint_checks()          | YES   | part of canonical pre-flight
canonical   | write_sprint_delta()            | YES   | part of canonical teardown
shell       | main() --sprint path            | NO    | delegates to run_sprint(), owns no sprint state
alternate   | main() --ct-pivot path          | NO    | CT log tool, no sprint
alternate   | main() --pivot path             | NO    | semantic pivot, no sprint
residual    | _get_live_feed_urls()           | NO    | shared helper, called by canonical

Canonical path: `python -m hledac.universal --sprint` → root main() --sprint
  → runtime.sprint_entrypoint.run_sprint() [sole canonical sprint owner]

  Note: `python -m hledac.universal.runtime.sprint_entrypoint --sprint` is an ALTERNATE entrypoint
  that also calls run_sprint() directly, but the canonical operator path
  is through root __main__.py (python -m hledac.universal).

Canonical sprint owner: run_sprint()
All report truth (canonical_run_summary, runtime_truth, timing_truth,
checkpoint_zero_category, observed_run_tuple) flows from run_sprint().

Usage:
    python -m hledac.universal.runtime.sprint_entrypoint --sprint --query "LockBit ransomware" --duration 1800
    python -m hledac.universal.core --ct-pivot example.com
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import hashlib
import logging
import os
import signal
import sys
import time
import traceback
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

# TYPE_CHECKING imports — available for type checker but NOT loaded at runtime
# This eliminates the 21.5s pre-loop import bottleneck
# Sprint F500I: Heavy modules loaded ONLY when --sprint actually runs
from typing import TYPE_CHECKING, Any

import httpx  # F4XX: replaces aiohttp

# Sprint S2: msgspec.Struct for SprintFlags (frozen) — 2-3× faster
# __init__ vs @dataclass, ~40B/instance smaller footprint, no GC tracking.
# M1 8GB friendly.
import msgspec
import orjson
from dotenv import load_dotenv

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    from hledac.universal.knowledge.semantic_store import SemanticStore
    from hledac.universal.runtime.scheduler_result import SprintSchedulerResult
    from hledac.universal.runtime.scheduler_v2 import SprintSchedulerV2 as SprintScheduler

# Runtime imports — lightweight, fast-loading only
from hledac.universal.evidence_log import EvidenceLog
from hledac.universal.core import memory_cycle as _memory_cycle  # F266-U2/U3


# =============================================================================
# PHASE REFACTORING: State Container & Decorators (F350M-R Phase 5)
# =============================================================================

@dataclass
class SprintRunContext:
    """
    PHASE REFACTORING F350M-R: Centralized state container for run_sprint phases.
    
    Replaces ~40+ local variables with a structured dataclass for better
    code organization, testability, and reduced cognitive load.
    
    Usage:
        ctx = SprintRunContext(sprint_id="...", phase_times={...})
        await _run_sprint_boot(ctx, query, ...)
        await _run_sprint_execute(ctx, query)
        await _run_sprint_windup(ctx, query)
        await _run_sprint_teardown(ctx)
    """
    # Phase timing
    phase_times: dict[str, float] = field(default_factory=dict)
    
    # Cancellation
    cancel_event: asyncio.Event | None = None
    
    # Sprint identity
    sprint_id: str = ""
    query_hash: str = ""
    
    # Resources
    store: "DuckDBShadowStore | None" = None
    scheduler: "SprintScheduler | None" = None
    power_assertion: Any = field(default=None)
    
    # Pre-flight state
    uma_baseline_gib: float = 0.0
    swap_detected_pre: bool = False
    uma_state_pre: str = "ok"
    effective_windup_s: float = 180.0
    
    # Recovery state
    resume_from: dict | None = None
    resume_step: int = 0
    
    # Seed state
    seed_state: Any = field(default=None)
    
    # Sprint results
    result: Any = field(default=None)
    intel: dict = field(default_factory=dict)
    
    # Evidence log
    evidence_log: Any = field(default=None)
    
    # Lock manager
    sprint_lock_mgr: Any = field(default=None)
    sprint_lock_path: Path | None = None
    
    # Export state
    report_path: Path | None = None
    live_feed_urls: list[str] = field(default_factory=list)
    
    # CT log client
    ct_log_client: Any = field(default=None)
    
    # Dashboard
    dashboard: Any = field(default=None)
    
    # DuckDB init flag
    duckdb_init_ok: bool = False
    
    # Execution parameters (set in execute phase)
    query: str = ""
    duration_s: float = 1800.0
    actual_duration: float = 0.0


@contextmanager
def _fail_safe(level: str = "debug", label: str = ""):
    """
    PHASE REFACTORING F350M-R: Standardized exception handling decorator.
    
    Replaces 39+ identical try/except/pass patterns with a consistent handler.
    - CancelledError is always re-raised (I6 invariant)
    - All other exceptions are logged at configured level and swallowed
    - Optional label for debugging which operation failed
    
    Usage:
        with _fail_safe("warning", "DuckDB init"):
            await store.async_initialize()
    
    Args:
        level: Log level - "debug", "info", "warning", "error"
        label: Descriptive label for the operation (for log messages)
    """
    try:
        yield
    except asyncio.CancelledError:
        raise  # I6 invariant: CancelledError must propagate, not swallowed
    except Exception as e:
        _log_level = {
            "debug": logger.debug,
            "info": logger.info,
            "warning": logger.warning,
            "error": logger.error,
        }.get(level, logger.debug)
        if label:
            _log_level(f"[fail_safe:{label}] {type(e).__name__}: {e}")
        else:
            _log_level(f"[fail_safe] {type(e).__name__}: {e}")


def _fail_safe_async(level: str = "debug", label: str = ""):
    """
    Async-compatible fail_safe wrapper for coroutines.
    """
    async def decorator(coro):
        try:
            return await coro
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log_level = {
                "debug": logger.debug,
                "info": logger.info,
                "warning": logger.warning,
                "error": logger.error,
            }.get(level, logger.debug)
            prefix = f"[fail_safe:{label}] " if label else "[fail_safe] "
            _log_level(f"{prefix}{type(e).__name__}: {e}")
            return None
    return decorator


# ULTIMATE-001: SprintSeedState global for deterministic cognitive replay
# Set by run_sprint() at sprint start, accessed by coordinators for seeded random
_current_sprint_seed_state: "SprintSeedState | None" = None


def get_sprint_seed_state() -> "SprintSeedState | None":
    """
    ULTIMATE-001: Get the current sprint's seed state for deterministic replay.

    Returns the SprintSeedState generated at sprint start, or None if called
    outside of a sprint context.

    Usage:
        seed_state = get_sprint_seed_state()
        if seed_state is not None:
            rng = random.Random(seed_state.prng_seed)
    """
    global _current_sprint_seed_state
    return _current_sprint_seed_state


from hledac.universal.core.resource_governor import (
    CLEAN_SWAP_MAX_GIB,
    HARD_BLOCK_SWAP_GIB,
    sample_uma_status,
)
from hledac.universal.graph.lock_manager import GraphLockManager  # F266-LOCK
from hledac.universal.paths import TOR_ROOT, get_sprint_json_report_path, get_sprint_lock_path
from hledac.universal.runtime.power_assertion import PowerAssertion  # APEX-1001
from hledac.universal.runtime.acquisition_strategy import (
    ACQUISITION_REPORT_SCHEMA_VERSION,
    SourceFamilyOutcome,
    build_acquisition_report,
    canonicalize_source_family_outcomes,
    complete_source_family_outcomes_from_lane_details,
    normalize_source_family_outcome,
    reconcile_lane_detail_fields,
)
from hledac.universal.runtime.acquisition_telemetry_reconcile import (
    complete_source_family_outcomes_from_prelude,
)
from hledac.universal.runtime.sprint_lifecycle import _PHASE_ORDER, SprintLifecycleManager
# A2: Lazy import to avoid circular import with composition_root (which imports
# _cancel_all_tasks from this module). build_runtime/run_runtime are resolved
# inside _run_sprint_loop at call time.
_build_runtime = None
_run_runtime = None
from hledac.universal.utils.async_helpers import (
    first_completed,
    parallel,
    parallel_ok,
    safe_create_task,
    safe_wait_for,
    _check_gathered,
)
from hledac.universal.utils.config_introspection import safe_attr_get

# E3: macOS P-core QoS — apply USER_INITIATED to main asyncio event loop thread.
# Rust rayon pools get this automatically (lib.rs:185-196 apply_qos_hint inside pool threads).
# Python asyncio runs on the main thread which needs the same hint.
from hledac.universal.utils.platform_info import apply_qos_to_main_thread

logger = logging.getLogger(__name__)

# Issue 10.2 / F320: Unified tracing + structured logging configuration.
# Single entry point replacing 4 separate setup calls:
#   - structlog with OTel trace context correlation
#   - OTel TracerProvider + exporters (stdout / OTLP / DuckDB)
#   - AsyncioInstrumentor (asyncio task/loop spans)
#   - Rust tracing bridge (tracing-opentelemetry → Python OTel pipeline)
#   - Logfire for local dev observability
#
# Always-on, fail-safe, M1 8GB safe. Idempotent — safe at module load.
try:
    from hledac.universal.runtime._telemetry_setup import configure

    configure()
except Exception:  # noqa: BLE001
    pass  # Never crash on tracing/logging init failure

# Lazy dual-import for backward-compatible API consumers.
try:
    from otel import (  # type: ignore
        instrumented as _otel_instrumented,
    )
except ImportError:  # production fallback (hledac.universal.otel namespace)
    from hledac.universal.otel import (
        instrumented as _otel_instrumented,
    )


# F221-ABORT: Minimum useful acquisition window below which the sprint produces
# no real evidence artifacts. Replicated from SprintSchedulerConfig.effective_windup_lead_s
# (F250: 30% of duration, clamp [30, 180]) so the guard rejects only what the
# scheduler would treat as zero-active-budget.
MIN_ACTIVE_WINDOW_S: int = 30


# ── Sprint F500-O: Report Serialization Optimization ─────────────────
# orjson.dumps optimization for large report_dict (~200+ keys).
# OPT_INDENT_2 adds ~10-20% overhead for pretty-printing — disable for production.
# Use HLEDAC_REPORT_PRETTY_PRINT=1 to enable pretty-printing for debugging.
_REPORT_SERIALIZE_OPTIONS: int = (
    orjson.OPT_INDENT_2
    if os.environ.get("HLEDAC_REPORT_PRETTY_PRINT", "0") == "1"
    else orjson.OPT_APPEND_NEWLINE
)

# Sprint F500-O: Pre-computed platform info — avoid repeated __import__ calls.
# Cached at module load to avoid import overhead during serialization.
_PLATFORM_INFO: dict[str, str] = {
    "python_version": sys.version.split()[0],
    "macos_version": None,
}
try:
    import platform as _platform_mod

    _PLATFORM_INFO["macos_version"] = _platform_mod.mac_ver()[0] or "unknown"
except Exception:
    _PLATFORM_INFO["macos_version"] = "unknown"


def _serialize_report(data: dict[str, Any]) -> bytes:
    """
    Optimized report serialization using orjson.

    - No indentation in production (faster, smaller files)
    - Appends newline for POSIX compliance
    - Uses OPT_SERIALIZE_NUMPY if numpy arrays present (auto-detected)
    """
    options = _REPORT_SERIALIZE_OPTIONS
    # Sprint F500-O: Try with NUMPY flag first for compatibility,
    # fall back to default if no numpy (faster path).
    try:
        import numpy

        options |= orjson.OPT_SERIALIZE_NUMPY
    except ImportError:  # noqa: BLE001
        pass  # numpy not available — use default path

    return orjson.dumps(data, option=options)


# ── Sprint S2: SprintFlags jako msgspec.Struct (frozen) ─────────────
# Puvodne @dataclass(frozen=True, slots=True). Msgspec.Struct advantages:
#   * Kompilovany `__init__` v C -> 2-3× rychlejsi konstrukce
#   * `` -> bez GC trackingu, mensi GC tlak v pre-flight guard
#   * `frozen=True` -> instance je nemenny snapshot po konstrukci
#   * Slotless storage (Struct internally uses C-level struct) -> ~40B/instance
#
# Konvence projektu: frozen +  pro immutable DTO v hot-path
# (viz SourceWork, FeedDominanceGuardResult, LaneBudgetAllocation).


class SprintFlags(msgspec.Struct, frozen=True, gc=False):
    """
    F221-ABORT + F26X-3 + F260: Bounded, immutable view of the CLI flags
    that gate pre-flight guards and layer-injection opt-outs. Mirrors the
    args Namespace fields required by run_sprint() and gives downstream
    seams (e.g. future advisory hooks) a typed contract instead of
    getattr-style probing.

    M1 memory friendly: frozen +  removes GC tracking + boxing
    (smaller per-instance footprint, less GC pressure during sprint cycles).

    Sprint F26X-3/F260 fix: this dataclass is now the SOLE carrier of
    layer-injection flags (no_communication/no_stealth/no_ghost) into
    run_sprint(). Replaces the previous getattr(args, "no_*", False)
    pattern that leaked the `args` namespace from main() — `args` is a
    local of argparse, never passed to run_sprint(), causing NameError.

    Keep this Struct minimal: only flags that affect pre-flight
    decisions or that callers consume as a coherent bundle belong here.
    Per-flag args stay in argparse.

    Sprint S2 (msgspec.Struct migration): attribute access, frozen, and
    default-arg construction all work identically to the prior @dataclass
    form. The only change is implementation: ~2-3× faster __init__ and
    ~40B/instance smaller footprint.
    """

    force: bool = False  # F221-ABORT: override zero-active-budget guard
    no_communication: bool = False  # F26X-3: skip CommunicationLayer injection
    no_stealth: bool = False  # F260: skip StealthLayer injection
    no_ghost: bool = False  # F260: skip GhostLayer injection
    no_coordination: bool = False  # F26X-2: skip CoordinationLayer (canonical contract)
    production: bool = False  # F272B: abort on fetch=NA in pre-flight (exit 2)
    hermes_force: bool = False  # F273D: force-load Hermes3 model even if HLEDAC_ENABLE_HERMES_SYNTHESIS != '1'
    blitz_mode: bool = False  # BLITZ-12: skip all stealth jitter delays (auto-enabled when duration ≤ 1800s)


def _make_sprint_id() -> str:
    """Generate collision-resistant sprint ID using ns timestamp + short uuid suffix."""
    ts = time.time_ns() // 1_000_000  # millisecond precision
    uid = uuid.uuid4().hex[:6]  # 6-char hex suffix
    return f"8sa_{ts}_{uid}"


def _is_meaningful_run(
    actual_duration_s: float,
    cycles_completed: int,
    cycles_started: int,
    accepted_findings: int,
    total_pattern_hits: int,
    swap_detected: bool = False,
    uma_state: str = "ok",
) -> tuple[bool, str]:
    """
    Distinguish smoke from meaningful active evidence.

    Returns (is_meaningful, evidence_note).
    Smoke: too short, too few cycles, no signal whatsoever.
    Meaningful: enough runtime or evidence of real work.

    F176A: Hardware-limited smoke detection — swap/memory pressure + zero cycles
    is a distinct hardware-limited classification, NOT depleted query.
    """
    # Hard smoke: no cycles ran at all
    if cycles_started == 0:
        # F176A: Explicit hardware-limited distinction
        if swap_detected or uma_state in ("critical", "emergency"):
            return False, "hardware_limited_smoke: zero cycles, memory pressure detected"
        return False, "zero cycles started — entry only, no active work"

    # Short but found something: counts as minimal meaningful
    if accepted_findings > 0:
        return True, f"found {accepted_findings} findings despite short runtime"

    # Short but pattern activity: minimal signal
    if total_pattern_hits > 0 and actual_duration_s >= 15:
        return True, f"pattern activity ({total_pattern_hits} hits) despite short run"

    # Hard smoke thresholds
    if actual_duration_s < 30 and cycles_completed < 3:
        return False, f"runtime {actual_duration_s:.0f}s and {cycles_completed} cycles below minimum"

    if actual_duration_s < 10:
        return False, f"runtime {actual_duration_s:.1f}s — entry/import only"

    # E0-T4: <180s without findings is meaningful_empty, not meaningful.
    # authoritative early-returns above (findings > 0, hits >= 15) are exempt.
    if actual_duration_s < 180 and accepted_findings == 0 and total_pattern_hits == 0:
        return False, (
            f"runtime {actual_duration_s:.0f}s < 180s floor, no findings, no pattern hits — below meaningful threshold"
        )

    # Normal meaningful run
    return True, (
        f"{actual_duration_s:.0f}s runtime, "
        f"{cycles_completed}/{cycles_started} cycles completed, "
        f"no findings but within normal parameters"
    )


# =============================================================================
# Issue #9: Schema-driven acquisition payload — replaces ~659-line
# _scheduler_result_acquisition_payload() triple-nested try/except chain with:
#   1. msgspec.convert(result, dict) — C-level ~50× faster than getattr chain
#   2. Single canonical try/except around build_acquisition_report()
#   3. One msgspec.Struct (AcqReportPayload) as the single output type
#
# Invariant: acq_payload_to_dict() — zero getattr on SprintSchedulerResult fields
#   after initial msgspec.convert. All field access via direct .attribute.
#   All defensive defaults encoded in AcqReportPayload field defaults.
# =============================================================================


class AcqReportPayload(msgspec.Struct, frozen=True, eq=False, gc=False):
    """
    [ISSUE-007] Schema-driven acquisition report — mirrors SprintSchedulerResult fields.

    M1 8GB: msgspec.Struct uses __slots__ — ~40 bytes/instance vs ~80 for dataclass,
    no GC header, direct C-level field access. frozen=True enables faster comparison.
    eq=False because we never compare payloads.

    PERFORMANCE NOTE (ISSUE-007): The 3ms msgspec.convert cost is paid ONCE per sprint
    at TEARDOWN — acceptable given the TEARDOWN budget. Splitting into sub-payloads
    would INCREASE allocation work (nested struct construction). The real optimization
    is avoiding unnecessary list()/dict() wrapping in acq_payload_to_dict.
    """

    # ── Canonical wrapper fields (returned by _scheduler_result_acquisition_payload) ──
    acquisition_report: dict[str, Any] = msgspec.field(default_factory=dict)
    acquisition_terminality_checked: bool = False
    acquisition_terminality_satisfied: bool = False
    acquisition_terminality_missing_lanes: list[str] = msgspec.field(default_factory=list)
    acquisition_terminality_report: dict[str, Any] = msgspec.field(default_factory=dict)
    source_family_outcomes: list[SourceFamilyOutcome] = msgspec.field(default_factory=list)
    scheduler_exit: dict[str, Any] = msgspec.field(default_factory=dict)
    return_guard: dict[str, Any] = msgspec.field(default_factory=dict)
    windup_guard_observation: dict[str, Any] = msgspec.field(default_factory=dict)
    prewindup_barrier: dict[str, Any] = msgspec.field(default_factory=dict)
    acquisition_prelude_checked: bool = False
    acquisition_prelude_ran: bool = False
    acquisition_prelude_required_lanes: list[str] = msgspec.field(default_factory=list)
    acquisition_prelude_terminal_lanes: list[str] = msgspec.field(default_factory=list)
    acquisition_prelude_missing_lanes: list[str] = msgspec.field(default_factory=list)
    acquisition_prelude_skipped_lanes: dict[str, str] = msgspec.field(default_factory=dict)
    acquisition_prelude_errors: dict[str, str] = msgspec.field(default_factory=dict)
    acquisition_prelude_duration_s: float = 0.0
    acquisition_prelude_reason: str = ""
    early_exit_class: str = ""
    early_exit_reason: str = ""
    requested_duration_s: float = 0.0
    actual_duration_s: float = 0.0
    elapsed_pct: float = 0.0
    active_window_budget_s: float = 0.0
    active_window_elapsed_s: float = 0.0

    # ── SprintSchedulerResult fields (msgspec.convert target) ──
    # FEED signal funnel
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
    entries_per_source: dict[str, int] = msgspec.field(default_factory=dict)
    hits_per_source: dict[str, int] = msgspec.field(default_factory=dict)
    final_phase: str = "BOOT"
    export_paths: list[str] = msgspec.field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""
    stop_requested: bool = False
    # Synthesis
    synthesis_success: bool = False
    synthesis_engine: str = "unknown"
    synthesis_findings_count: int = 0
    ioc_cooccurrence_edges: int = 0
    synthesis_text: str = ""
    hypotheses_generated: int = 0
    pii_findings_anonymized: int = 0
    # PUBLIC
    public_discovered: int = 0
    public_fetched: int = 0
    public_matched_patterns: int = 0
    public_accepted_findings: int = 0
    public_stored_findings: int = 0
    public_error: str = ""
    public_provider_selection_debug: dict[str, Any] = msgspec.field(default_factory=dict)
    public_terminal_stage: str = ""
    public_stage_counters: dict[str, Any] = msgspec.field(default_factory=dict)
    public_discovery_empty_reason: str = ""
    public_discovery_debug_reason: str = ""
    public_backend_degraded: bool = False
    dominant_public_blocker: str = ""
    public_bootstrap_order: tuple[str, ...] = msgspec.field(default_factory=tuple)
    public_bootstrap_prevented_discovery_timeout: bool = False
    public_bootstrap_first_fetch_attempted: bool = False
    # CT log
    ct_log_discovered: int = 0
    ct_log_stored: int = 0
    ct_log_accepted_findings: int = 0
    ct_log_error: str = ""
    ct_loss_stage: str = ""
    ct_bridge_invoked: bool = False
    ct_raw_sample_keys: tuple[str, ...] = msgspec.field(default_factory=tuple)
    ct_raw_sample_count: int = 0
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
    ct_candidate_examples: list[str] = msgspec.field(default_factory=list)
    ct_bridge_rejections_count: int = 0
    ct_bridge_rejection_reasons: dict[str, int] = msgspec.field(default_factory=dict)
    ct_candidates_accumulated: int = 0
    ct_candidates_stored: int = 0
    ct_storage_rejected: int = 0
    ct_storage_rejection_reasons: dict[str, int] = msgspec.field(default_factory=dict)
    quality_rejection_ledger: dict[str, Any] = msgspec.field(default_factory=dict)
    quality_rejection_summary_by_family: dict[str, Any] = msgspec.field(default_factory=dict)
    duplicate_rejection_summary_by_family: dict[str, Any] = msgspec.field(default_factory=dict)
    low_information_by_family: dict[str, Any] = msgspec.field(default_factory=dict)
    ct_quarantine_count: int = 0
    ct_quarantine_samples: list[str] = msgspec.field(default_factory=list)
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
    ct_candidates_built: int = 0
    ct_storage_attempted: bool = False
    ct_storage_accepted: bool = False
    ct_terminal_stage: str = ""
    ct_prelude_missing_but_final_attempted: bool = False
    # Timing
    entered_active_at_monotonic: float = 0.0
    pre_loop_elapsed_s: float = 0.0
    first_cycle_started_at_monotonic: float = 0.0
    pre_active_starved: bool = False
    pre_loop_blocker_reason: str = ""
    dedup_preload_count: int = 0
    dedup_preload_elapsed_s: float = 0.0
    feed_zero_yield_detected: bool = False
    feed_inaccessible_detected: bool = False
    feed_content_empty_detected: bool = False
    feed_no_pattern_with_content: bool = False
    findings_build_loss_detected: bool = False
    feed_no_signal_sources: bool = False
    policy_quality_feedback_calls: int = 0
    policy_quality_feedback_decisions: int = 0
    policy_quality_feedback_sources: int = 0
    policy_quality_feedback_errors: int = 0
    public_backend_degraded_flag: bool = False
    dominant_feed_blocker: str = ""
    dominant_branch_blocker: str = ""
    branch_degradation_summary: dict[str, Any] = msgspec.field(default_factory=dict)
    branch_timeout_count: int = 0
    branch_skipped_remaining_too_low: int = 0
    public_branch_timed_out: bool = False
    ct_branch_timed_out: bool = False
    findings_deduplicated: int = 0
    hypothesis_contradictions_detected: int = 0
    cover_traffic_fired: bool = False
    hermes_model_loaded: bool = False
    hermes_load_attempted: bool = False
    hermes_load_reason: str = ""
    hermes_load_elapsed_s: float = 0.0
    mlx_batcher_stats: dict[str, Any] = msgspec.field(default_factory=dict)
    pattern_extraction_drain_completed: bool = False
    pattern_extraction_drain_timed_out: bool = False
    pattern_extraction_drain_elapsed_s: float = 0.0
    malloc_pressure_relief_count: int = 0
    malloc_pressure_relief_last_rc: int = 0
    malloc_pressure_relief_last_at_s: float = 0.0
    dynamic_branch_floor_s: float = 0.0
    effective_windup_lead_used_s: float = 0.0
    windup_lead_adaptive_factor: float = 0.0
    captcha_hits: int = 0
    circuit_breaker_opens: int = 0
    rl_suggested_pivot: str = ""
    duckdb_mode: str = ""
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
    sidecars_skipped: int = 0
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
    rdap_enrichment_error: str = ""
    security_rejected_count: int = 0
    pii_redacted_count: int = 0
    rl_enabled: bool = False
    rl_epsilon: float = 0.0
    rl_total_reward: float = 0.0
    rl_last_action: str = ""
    rl_lane_combo: str = ""
    acquisition_lane_outcomes: tuple[Any, ...] = msgspec.field(default_factory=tuple)
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
    doh_provider_errors: tuple[str, ...] = msgspec.field(default_factory=tuple)
    doh_cache_used: bool = False
    doh_seed_source: str = ""
    wayback_attempted: int = 0
    wayback_raw_count: int = 0
    wayback_candidates_built: int = 0
    wayback_accepted_count: int = 0
    wayback_terminal_state: str = ""
    wayback_unchanged_rejected: int = 0
    graph_rag_context_count: int = 0
    passive_dns_attempted: int = 0
    passive_dns_raw_count: int = 0
    passive_dns_candidates_built: int = 0
    passive_dns_accepted_count: int = 0
    passive_dns_terminal_state: str = ""
    wayback_advisory_clues_count: int = 0
    wayback_changed_url_count: int = 0
    wayback_added_url_count: int = 0
    wayback_digest_changed_count: int = 0
    nonfeed_predispatch_attempted: bool = False
    nonfeed_predispatch_skipped: bool = False
    nonfeed_predispatch_lanes: list[str] = msgspec.field(default_factory=list)
    nonfeed_predispatch_duration_s: float = 0.0
    windup_blocked_until_nonfeed_attempted: bool = False
    nonfeed_plan_debug: dict[str, Any] = msgspec.field(default_factory=dict)
    prewindup_barrier_checked: bool = False
    prewindup_barrier_required_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    prewindup_barrier_satisfied: bool = False
    prewindup_barrier_attempted_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    prewindup_barrier_skipped_lanes: dict[str, str] = msgspec.field(default_factory=dict)
    prewindup_barrier_errors: dict[str, str] = msgspec.field(default_factory=dict)
    prewindup_barrier_duration_s: float = 0.0
    windup_delayed_for_nonfeed: bool = False
    prewindup_barrier_delayed_cycle: int = 0
    windup_guard_call_count: int = 0
    windup_guard_callback_supplied_count: int = 0
    windup_guard_callback_executed_count: int = 0
    windup_guard_last_reason: str = ""
    windup_guard_last_phase: str = ""
    windup_guard_last_allowed: bool = False
    windup_guard_last_callback_not_executed_reason: str = ""
    windup_guard_not_applicable: bool = False
    windup_guard_required_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    prewindup_guard_async_bridge_used: bool = False
    prewindup_guard_async_error: str = ""
    prewindup_guard_fail_closed: bool = False
    return_guard_checked: bool = False
    return_guard_required_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    return_guard_satisfied: bool = False
    return_guard_delayed_for_nonfeed: bool = False
    return_guard_block_reason: str = ""
    return_guard_attempted_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    return_guard_skipped_lanes: dict[str, str] = msgspec.field(default_factory=dict)
    return_guard_errors: dict[str, str] = msgspec.field(default_factory=dict)
    dark_surface_pivots_attempted: int = 0
    dark_surface_pivots_accepted: int = 0
    gopher_findings_ingested: int = 0
    bgp_enrichment_findings_ingested: int = 0
    banner_grab_findings_ingested: int = 0
    scheduler_exit_path: str = ""
    scheduler_exit_reason: str = ""
    scheduler_exit_phase: str = ""
    scheduler_exit_cycle: int = 0
    scheduler_exit_elapsed_s: float = 0.0
    scheduler_exit_guard_checked: bool = False
    scheduler_exit_guard_required: bool = False
    scheduler_exit_guard_satisfied: bool = False
    hard_deadline_monotonic: float = 0.0
    hard_deadline_checked_count: int = 0
    hard_deadline_exceeded: bool = False
    hard_deadline_exceeded_at_cycle: int = 0
    hard_deadline_remaining_s_at_exit: float = 0.0
    acquisition_terminality_checked_flag: bool = False
    acquisition_terminality_satisfied_flag: bool = False
    acquisition_terminality_missing_lanes_list: list[str] = msgspec.field(default_factory=list)
    acquisition_terminality_report_dict: dict[str, Any] = msgspec.field(default_factory=dict)
    nonfeed_predispatch_checked: bool = False
    nonfeed_predispatch_ran: bool = False
    nonfeed_predispatch_reason: str = ""
    nonfeed_predispatch_outcomes_count: int = 0
    acquisition_prelude_checked_flag: bool = False
    acquisition_prelude_ran_flag: bool = False
    acquisition_prelude_required_lanes_list: list[str] = msgspec.field(default_factory=list)
    acquisition_prelude_terminal_lanes_list: list[str] = msgspec.field(default_factory=list)
    acquisition_prelude_missing_lanes_list: list[str] = msgspec.field(default_factory=list)
    acquisition_prelude_skipped_lanes_dict: dict[str, str] = msgspec.field(default_factory=dict)
    acquisition_prelude_errors_dict: dict[str, str] = msgspec.field(default_factory=dict)
    acquisition_prelude_duration_s_float: float = 0.0
    acquisition_prelude_reason_str: str = ""
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
    feed_suppressed_by_budget: bool = False
    feed_budget_per_source: dict[str, int] = msgspec.field(default_factory=dict)
    top_feed_source_counts: dict[str, int] = msgspec.field(default_factory=dict)
    max_per_source_applied: int = 0
    nonfeed_budget_active: bool = False
    nonfeed_budget_expected_lanes: list[str] = msgspec.field(default_factory=list)
    nonfeed_budget_terminal_lanes: list[str] = msgspec.field(default_factory=list)
    nonfeed_budget_unresolved_lanes: list[str] = msgspec.field(default_factory=list)
    feed_suppressed_by_nonfeed_budget: bool = False
    feed_suppression_count: int = 0
    feed_suppression_reason: str = ""
    nonfeed_prelude_enabled: bool = False
    nonfeed_prelude_expected_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    nonfeed_prelude_attempted_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    nonfeed_prelude_terminal_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    nonfeed_prelude_missing_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    nonfeed_prelude_accepted_by_lane: dict[str, int] = msgspec.field(default_factory=dict)
    nonfeed_prelude_error_by_lane: dict[str, str] = msgspec.field(default_factory=dict)
    nonfeed_prelude_duration_s: float = 0.0
    nonfeed_prelude_feed_blocked_until_complete: bool = False
    nonfeed_priority_enabled: bool = False
    nonfeed_profile_expected_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    nonfeed_expected_lanes: list[str] | None = None
    nonfeed_missing_expected_lanes: list[str] | None = None
    nonfeed_expected_lanes_source: str = ""
    feed_domain_seeds: tuple[str, ...] = msgspec.field(default_factory=tuple)
    arrow_batch_hard_cap: int = 0
    arrow_batch_dropped: int = 0
    arrow_flush_failure_count: int = 0
    arrow_last_flush_error: str = ""
    arrow_metrics: dict[str, Any] = msgspec.field(default_factory=dict)
    transport_efficiency: float = 0.0
    pivot_lane_plan_count: int = 0
    planned_pivot_lanes: list[str] = msgspec.field(default_factory=list)
    seed_quality_checked: bool = False
    seed_quality_keep_count: int = 0
    seed_quality_drop_count: int = 0
    seed_quality_drop_reasons: list[str] = msgspec.field(default_factory=list)
    seed_quality_kept_sample: list[str] = msgspec.field(default_factory=list)
    seed_quality_dropped_sample: list[str] = msgspec.field(default_factory=list)
    seed_quality_bypass_reason: str = ""
    requested_duration_s_r: float = 0.0
    actual_duration_s_r: float = 0.0
    elapsed_pct_r: float = 0.0
    active_window_budget_s_r: float = 0.0
    active_window_elapsed_s_r: float = 0.0
    windup_efficiency: float = 0.0
    early_exit_class_s: str = ""
    early_exit_reason_s: str = ""
    source_family_events: list[str] = msgspec.field(default_factory=list)
    MAX_SOURCE_FAMILY_EVENTS: int = 0
    feed_dominance_ratio: float = 0.0
    feed_dominance_class: str = ""
    feed_dominance_guard_triggered: bool = False
    should_recommend_nonfeed_diagnostic: bool = False
    nonfeed_mission_active: bool = False
    nonfeed_required_families: tuple[str, ...] = msgspec.field(default_factory=tuple)
    nonfeed_optional_families: tuple[str, ...] = msgspec.field(default_factory=tuple)
    nonfeed_family_status: dict[str, str] = msgspec.field(default_factory=dict)
    nonfeed_all_required_terminal: bool = False
    nonfeed_any_accepted: bool = False
    nonfeed_provider_failures: tuple[str, ...] = msgspec.field(default_factory=tuple)
    nonfeed_memory_skips: int = 0
    nonfeed_mission_exit_reason: str = ""
    nonfeed_candidate_ledger_summary: dict[str, Any] = msgspec.field(default_factory=dict)
    nonfeed_lane_eligibility: dict[str, Any] = msgspec.field(default_factory=dict)
    nonfeed_doh_planner_input: dict[str, Any] = msgspec.field(default_factory=dict)
    nonfeed_ct_planner_candidates: dict[str, Any] = msgspec.field(default_factory=dict)
    nonfeed_wayback_candidates: dict[str, Any] = msgspec.field(default_factory=dict)
    nonfeed_passive_dns_candidates: dict[str, Any] = msgspec.field(default_factory=dict)
    research_context: str = ""
    acquisition_plan_present_for_prelude: bool = False
    acquisition_plan_lanes_for_prelude: tuple[str, ...] = msgspec.field(default_factory=tuple)
    acquisition_plan_enabled_lanes_for_prelude: tuple[str, ...] = msgspec.field(default_factory=tuple)
    acquisition_plan_profile_for_prelude: str = ""
    acquisition_plan_build_error_for_prelude: str = ""
    timer_events: list[str] = msgspec.field(default_factory=list)
    seed_context_available: bool = False
    seed_context_propagated: bool = False
    lanes_unlocked_by_seed_context: list[str] = msgspec.field(default_factory=list)
    seed_context_skip_reason: str = ""
    seed_context_source: str = ""
    pivot_seed_count: int = 0
    pivot_seed_type_counts: dict[str, int] = msgspec.field(default_factory=dict)
    pivot_seed_sample: list[str] = msgspec.field(default_factory=list)
    pivot_seed_domains: tuple[str, ...] = msgspec.field(default_factory=tuple)
    pivot_seed_ips: tuple[str, ...] = msgspec.field(default_factory=tuple)
    pivot_seed_urls: tuple[str, ...] = msgspec.field(default_factory=tuple)
    pivot_seed_hashes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    pivot_seed_cves: tuple[str, ...] = msgspec.field(default_factory=tuple)
    next_seeds_query_suggestions: list[str] = msgspec.field(default_factory=list)
    next_seeds_skip_reason: str = ""
    next_seeds_ioc_domains: list[str] = msgspec.field(default_factory=list)
    next_seeds_ioc_ips: list[str] = msgspec.field(default_factory=list)
    next_seeds_ioc_urls: list[str] = msgspec.field(default_factory=list)
    next_seeds_ioc_hashes: list[str] = msgspec.field(default_factory=list)
    next_seeds_ioc_cves: list[str] = msgspec.field(default_factory=list)
    next_seeds_provider_yield: float = 0.0
    next_seeds_pivot_deepening: bool = False
    next_seeds_consumed_count: int = 0
    next_seeds_seed_source: str = ""
    planner_actions_consumed_count: int = 0
    planner_action_lanes_requested: tuple[str, ...] = msgspec.field(default_factory=tuple)
    planner_action_seed_source: str = ""
    planner_action_skip_reason: str = ""
    quantum_path_seeds: list[str] = msgspec.field(default_factory=list)
    run_error_class: str = ""
    run_error: str = ""
    pivot_graph_stats_used: bool = False
    pivot_graph_stats_keys: tuple[str, ...] = msgspec.field(default_factory=tuple)
    graph_aware_pivot_count: int = 0
    pivot_integration_reason: str = ""
    findings: list[Any] = msgspec.field(default_factory=list)


# =============================================================================
# Issue #11: AcqReportPayload Memory Optimization for M1 8GB
#
# DECISION: Keep single AcqReportPayload (splitting rejected - would INCREASE
# allocation work with nested struct construction per Issue-007 comment).
#
# OPTIMIZATION STRATEGY for M1 8GB:
#   1. TypedDict-based output types — type-safe dict serialization
#   2. msgspec.to_builtins() direct serialization — avoid intermediate copies
#   3. Lazy property accessors — typed views without data copying
#   4. __slots__ already active (msgspec.Struct default) — ~40 bytes/instance
# =============================================================================
if TYPE_CHECKING:
    from typing import TypedDict

    class AcqReportTimingDict(TypedDict, total=False):
        """Timing-related fields for export. TypedDict for type-safe serialization."""
        entered_active_at_monotonic: float
        pre_loop_elapsed_s: float
        first_cycle_started_at_monotonic: float
        pre_loop_blocker_reason: str
        dedup_preload_elapsed_s: float
        windup_guard_last_reason: str
        windup_guard_last_phase: str
        windup_guard_not_applicable: bool
        windup_guard_last_callback_not_executed_reason: str
        prewindup_barrier_duration_s: float
        return_guard_checked: bool
        return_guard_satisfied: bool
        scheduler_exit_elapsed_s: float
        actual_duration_s: float
        elapsed_pct: float
        windup_efficiency: float
        transport_efficiency: float

    class AcqReportFindingsDict(TypedDict, total=False):
        """Finding count fields for export."""
        cycles_started: int
        cycles_completed: int
        accepted_findings: int
        findings_built_pre_store: int
        findings_deduplicated: int
        synthesis_findings_count: int
        public_accepted_findings: int
        ct_log_accepted_findings: int
        lane_ct_accepted_findings: int
        lane_wayback_accepted_findings: int
        lane_pdns_accepted_findings: int

    class AcqReportPublicDict(TypedDict, total=False):
        """PUBLIC-related fields for export."""
        public_discovered: int
        public_fetched: int
        public_matched_patterns: int
        public_accepted_findings: int
        public_stored_findings: int
        public_error: str
        public_terminal_stage: str
        public_discovery_empty_reason: str
        dominant_public_blocker: str
        public_backend_degraded: bool

    class AcqReportCTDict(TypedDict, total=False):
        """CT log-related fields for export."""
        ct_log_discovered: int
        ct_log_stored: int
        ct_log_accepted_findings: int
        ct_log_error: str
        ct_candidate_count: int
        ct_valid_domain_count: int
        ct_bridge_build_success_count: int
        ct_bridge_quality_rejected_count: int
        ct_raw_domains_seen: int
        ct_unique_domains_seen: int
        ct_wildcard_domains: int
        ct_private_reserved_domains: int
        ct_duplicate_candidates: int
        ct_candidates_accumulated: int
        ct_candidates_stored: int
        ct_storage_rejected: int
        ct_provider_status: str
        ct_terminal_stage: str

del TYPE_CHECKING  # Clean up namespace — TypedDict used only for type hints


def _get_timing_fields(r: AcqReportPayload) -> dict[str, Any]:
    """
    [ISSUE-011] Memory-efficient timing field extraction.
    
    Returns a dict with timing-related fields WITHOUT full msgspec.to_builtins().
    Uses direct attribute access — zero getattr overhead, minimal allocation.
    
    M1 8GB: ~50 fields extracted with single dict literal, no intermediate copies.
    """
    return {
        # Core timing
        "entered_active_at_monotonic": r.entered_active_at_monotonic,
        "pre_loop_elapsed_s": r.pre_loop_elapsed_s,
        "first_cycle_started_at_monotonic": r.first_cycle_started_at_monotonic,
        "actual_duration_s": r.actual_duration_s,
        "elapsed_pct": r.elapsed_pct,
        "active_window_elapsed_s": r.active_window_elapsed_s,
        "requested_duration_s": r.requested_duration_s,
        "active_window_budget_s": r.active_window_budget_s,
        # Pre-loop
        "pre_loop_blocker_reason": r.pre_loop_blocker_reason,
        "pre_active_starved": r.pre_active_starved,
        "dedup_preload_count": r.dedup_preload_count,
        "dedup_preload_elapsed_s": r.dedup_preload_elapsed_s,
        # Windup guard
        "windup_guard_call_count": r.windup_guard_call_count,
        "windup_guard_required_lanes": r.windup_guard_required_lanes,
        "windup_guard_not_applicable": r.windup_guard_not_applicable,
        "windup_guard_last_reason": r.windup_guard_last_reason,
        "windup_guard_last_phase": r.windup_guard_last_phase,
        "windup_guard_last_allowed": r.windup_guard_last_allowed,
        "windup_guard_last_callback_not_executed_reason": r.windup_guard_last_callback_not_executed_reason,
        "windup_guard_callback_supplied_count": r.windup_guard_callback_supplied_count,
        "windup_guard_callback_executed_count": r.windup_guard_callback_executed_count,
        "windup_efficiency": r.windup_efficiency,
        "effective_windup_lead_used_s": r.effective_windup_lead_used_s,
        "windup_lead_adaptive_factor": r.windup_lead_adaptive_factor,
        # Prewindup barrier
        "prewindup_barrier_checked": r.prewindup_barrier_checked,
        "prewindup_barrier_satisfied": r.prewindup_barrier_satisfied,
        "prewindup_barrier_duration_s": r.prewindup_barrier_duration_s,
        "windup_delayed_for_nonfeed": r.windup_delayed_for_nonfeed,
        # Transport
        "transport_efficiency": r.transport_efficiency,
    }


def _get_memory_fields(r: AcqReportPayload) -> dict[str, Any]:
    """
    [ISSUE-011] Memory-efficient memory/governor field extraction.
    
    Returns a dict with memory-related fields for M1 8GB monitoring.
    """
    return {
        "peak_rss_gib": r.peak_rss_gib,
        "governor_uma_state": r.governor_uma_state,
        "governor_system_used_gib": r.governor_system_used_gib,
        "governor_swap_detected": r.governor_swap_detected,
        "governor_io_only": r.governor_io_only,
        "malloc_pressure_relief_count": r.malloc_pressure_relief_count,
        "malloc_pressure_relief_last_rc": r.malloc_pressure_relief_last_rc,
        "malloc_pressure_relief_last_at_s": r.malloc_pressure_relief_last_at_s,
        "pressure_violations": r.pressure_violations,
        "budget_violations": r.budget_violations,
    }


def _serialize_payload_direct(r: AcqReportPayload) -> dict[str, Any]:
    """
    [ISSUE-011] Memory-efficient payload serialization using msgspec.to_builtins().
    
    M1 8GB OPTIMIZATION:
    - Uses msgspec.to_builtins() directly — avoids intermediate msgspec.Struct instantiation
    - Single-pass conversion to dict for serialization
    - No extra list()/dict() wrapping (per ISSUE-007 optimization)
    
    For large payloads (~250 fields), this is ~3-5× faster than getattr chain
    and uses less peak memory than repeated dict construction.
    """
    return msgspec.to_builtins(r)


def _normalize_seed_context(
    report: dict[str, Any],
    r: AcqReportPayload,
) -> dict[str, Any]:
    """
    Sprint F350M-R: Normalize seed context fields from AcqReportPayload.

    Extracts duplicated logic from success and fallback paths in acq_payload_to_dict.
    Returns updated report dict.
    """
    if not report.get("seed_context_available"):
        has_seeds = (
            r.pivot_seed_domains
            or r.pivot_seed_ips
            or r.pivot_seed_urls
            or r.pivot_seed_hashes
            or r.pivot_seed_cves
        )
        if has_seeds:
            report["seed_context_available"] = True
            report["seed_context_propagated"] = r.seed_context_propagated
            if not report.get("seed_context_skip_reason"):
                report["seed_context_skip_reason"] = ""
        else:
            if not report.get("seed_context_skip_reason"):
                report["seed_context_skip_reason"] = "no_runtime_pivot_seeds"
    return report


def _build_sfo_list(r: AcqReportPayload) -> list[SourceFamilyOutcome]:
    """
    Build source_family_outcomes list from AcqReportPayload.

    ISSUE 23: Returns list[SourceFamilyOutcome] — attribute access instead of
    dict.get(), 3× faster per-field access in normalize_source_family_outcome.
    Conversion to list[dict] happens at the call site (line 866).
    """
    sfo_list: list[SourceFamilyOutcome] = []

    # FEED
    if r.accepted_findings > 0 or r.total_pattern_hits > 0:
        sfo_list.append(
            normalize_source_family_outcome(
                "FEED",
                {
                    "family": "FEED",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": r.total_pattern_hits,
                    "built_count": 0,
                    "accepted_count": r.accepted_findings,
                    "error": None,
                    "timeout": False,
                    "duration_s": None,
                },
            )
        )

    # PUBLIC
    pub_pts = r.public_terminal_stage
    pub_fetch_attempted = bool(r.public_stage_counters and r.public_stage_counters.get("fetch_attempted", 0) > 0)
    pub_has_outcome = bool(
        r.public_discovered > 0
        or r.public_accepted_findings > 0
        or (pub_pts and pub_pts != "NOT_SCHEDULED")
        or r.public_error
        or pub_fetch_attempted
    )
    if pub_has_outcome:
        sfo_list.append(
            normalize_source_family_outcome(
                "PUBLIC",
                {
                    "family": "PUBLIC",
                    "attempted": True,
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": r.public_discovered,
                    "built_count": 0,
                    "accepted_count": r.public_accepted_findings,
                    "error": r.public_error or r.public_terminal_stage or None,
                    "timeout": r.public_terminal_stage == "DISCOVERY_TIMEOUT",
                    "duration_s": None,
                },
            )
        )

    # CT log
    ct_has_outcome = bool(
        r.ct_log_discovered > 0
        or r.ct_log_accepted_findings > 0
        or r.ct_terminal_stage
        or r.ct_log_error
        or r.ct_planned
        or r.ct_scheduled
        or r.ct_request_attempted
        or r.ct_provider_status
    )
    if ct_has_outcome:
        sfo_list.append(
            normalize_source_family_outcome(
                "CT",
                {
                    "family": "CT",
                    "attempted": bool(r.ct_request_attempted or r.ct_scheduled or r.ct_planned),
                    "skipped": False,
                    "skip_reason": None,
                    "raw_count": r.ct_log_discovered,
                    "built_count": 0,
                    "accepted_count": r.ct_log_accepted_findings,
                    "error": r.ct_log_error or r.ct_terminal_stage or None,
                    "timeout": r.ct_terminal_stage == "request_timeout",
                    "duration_s": None,
                },
            )
        )

    # Map acquisition_lane_outcomes
    lanes_seen: set[str] = set()
    for _o in r.acquisition_lane_outcomes or ():
        if not hasattr(_o, "lane"):
            continue
        _lane = _o.lane
        if _lane in lanes_seen:
            continue
        lanes_seen.add(_lane)
        sfo_list.append(
            normalize_source_family_outcome(
                getattr(_o, "source_family", _lane.upper()),
                {
                    "family": getattr(_o, "source_family", _lane.upper()),
                    "attempted": getattr(_o, "attempted", False),
                    "skipped": not getattr(_o, "attempted", False),
                    "skip_reason": None if getattr(_o, "attempted", False) else "lane_not_attempted",
                    "raw_count": getattr(_o, "ct_results_raw", 0),
                    "built_count": getattr(_o, "ct_candidates_built", 0),
                    "accepted_count": getattr(_o, "accepted_findings", 0),
                    "error": getattr(_o, "error", None),
                    "timeout": getattr(_o, "timeout", False),
                    "duration_s": getattr(_o, "duration_s", None),
                },
            )
        )

    return canonicalize_source_family_outcomes(sfo_list)


def acq_payload_to_dict(result: Any, scheduler: Any, query: str, _duration_s: float) -> dict[str, Any]:
    """
    [Issue #9] Schema-driven acquisition payload.

    Replaces ~659-line _scheduler_result_acquisition_payload() triple-nested
    try/except chain with:
      1. msgspec.convert(result, AcqReportPayload) — C-level validation,
         ~50× faster than 31 getattr calls + defensive defaults.
      2. Single canonical try/except around build_acquisition_report().
      3. Direct .attribute access on AcqReportPayload — zero getattr.

    [Issue #11] M1 8GB Memory Optimization:
      - Fallback path uses msgspec.to_builtins() instead of msgspec.to_dict()
      - Helper functions _get_timing_fields()/_get_memory_fields() for lazy field groups
      - _serialize_payload_direct() for direct serialization without intermediate copies
      - TypedDict-based output types for type-safe serialization

    All defensive defaults are encoded in AcqReportPayload field definitions.

    Args:
        result: SprintSchedulerResult instance
        scheduler: SprintScheduler instance
        query: sprint query string
        duration_s: actual sprint duration

    Returns:
        dict with all acquisition report fields (see AcqReportPayload docstring)
    """
    # ── 1. Schema-driven conversion — C-level, no Python getattr chain ──────────
    r: AcqReportPayload
    try:
        r = msgspec.convert(result, AcqReportPayload)
    except Exception as _conv_exc:
        logger.exception(
            "[Issue9] msgspec.convert(SprintSchedulerResult->AcqReportPayload) failed: %s",
            _conv_exc,
        )
        # Last-resort fallback: zero-filled payload
        r = AcqReportPayload()

    # ── 2. Build source_family_outcomes (direct attribute access) ─────────────────
    sfo_list = _build_sfo_list(r)

    # ── 3. Scheduler exit ─────────────────────────────────────────────────────
    se_dict = {
        "exit_path": r.scheduler_exit_path,
        "exit_reason": r.scheduler_exit_reason,
        "exit_phase": r.scheduler_exit_phase,
        "exit_cycle": r.scheduler_exit_cycle,
        "exit_elapsed_s": r.scheduler_exit_elapsed_s,
        "exit_guard_checked": r.scheduler_exit_guard_checked,
        "exit_guard_satisfied": r.scheduler_exit_guard_satisfied,
    }

    # ── 4. Return guard ──────────────────────────────────────────────────────
    # [ISSUE-007] Avoid unnecessary list()/dict() wrapping — msgspec.Struct fields
    # are already the correct type after convert. Using r.field directly is faster.
    rg_dict = {
        "return_guard_checked": r.return_guard_checked,
        "return_guard_satisfied": r.return_guard_satisfied,
        "return_guard_block_reason": r.return_guard_block_reason,
        "return_guard_attempted_lanes": r.return_guard_attempted_lanes,
        "return_guard_skipped_lanes": r.return_guard_skipped_lanes,
        "return_guard_errors": r.return_guard_errors,
        "return_guard_delayed_for_nonfeed": r.return_guard_delayed_for_nonfeed,
    }

    # ── 5. Windup guard observation ───────────────────────────────────────────
    wg_dict = {
        "windup_guard_call_count": r.windup_guard_call_count,
        "windup_guard_callback_supplied_count": r.windup_guard_callback_supplied_count,
        "windup_guard_callback_executed_count": r.windup_guard_callback_executed_count,
        "windup_guard_required_lanes": r.windup_guard_required_lanes,
        "windup_guard_not_applicable": r.windup_guard_not_applicable,
        "windup_guard_last_reason": r.windup_guard_last_reason,
        "windup_guard_last_allowed": r.windup_guard_last_allowed,
        "windup_guard_callback_not_executed_reason": r.windup_guard_last_callback_not_executed_reason,
    }

    # ── 6. Prewindup barrier ─────────────────────────────────────────────────
    pwb = {
        "checked": r.prewindup_barrier_checked,
        "satisfied": r.prewindup_barrier_satisfied,
        "required_lanes": r.prewindup_barrier_required_lanes,
        "attempted_lanes": r.prewindup_barrier_attempted_lanes,
        "skipped_lanes": r.prewindup_barrier_skipped_lanes,
        "errors": r.prewindup_barrier_errors,
        "duration_s": r.prewindup_barrier_duration_s,
        "windup_delayed": r.windup_delayed_for_nonfeed,
        "nonfeed_scheduler_gap_resolved": getattr(result, "nonfeed_scheduler_gap_resolved", False),
    }

    # ── 7. Acquisition terminality ────────────────────────────────────────────
    term_rep: dict[str, Any] = r.acquisition_terminality_report or {}

    # ── 8. Acquisition plan / nonfeed debug (direct scheduler attribute access) ─
    plan = getattr(scheduler, "_acquisition_plan", None)
    nd_raw = getattr(plan, "nonfeed_plan_debug", None) if plan else None
    cfg = getattr(scheduler, "_config", None)
    cfg_profile = safe_attr_get(cfg, "acquisition_profile", None) if cfg else None
    # Extract acquisition_profile before replacing nd_raw with dict
    profile_from_nd = getattr(nd_raw, "acquisition_profile", None) if nd_raw else None
    acq_effective = profile_from_nd or cfg_profile or "default"

    # Single helper call replaces 14 individual getattr on nd_raw
    nd: dict[str, Any] | None = _extract_nonfeed_debug_fields(nd_raw)

    # ── 9. Canonical build_acquisition_report — single try/except ──────────────
    __acq_report: dict[str, Any] = {}
    _acq_profile = safe_attr_get(nd, "acquisition_profile", "default") if nd else (acq_effective or "default")
    _feed_cap_reason = nd.get("feed_cap_reason") if nd else None
    _nonfeed_priority_enabled = nd.get("nonfeed_priority_enabled", False) if nd else (acq_effective == "nonfeed_diagnostic")
    _nonfeed_profile_expected_lanes = (
        nd.get("nonfeed_profile_expected_lanes", [])
        if nd
        else (
            ["CT", "WAYBACK", "PASSIVE_DNS", "PIVOT_EXECUTOR", "DOH"]
            if acq_effective in ("nonfeed_diagnostic", "deep_osint_m1")
            else []
        )
    )
    try:
        _acq_report = build_acquisition_report(
            plan=plan,
            terminality=term_rep,
            nonfeed_plan_debug=nd,
            source_family_outcomes=sfo_list,
            return_guard=rg_dict,
            prewindup_barrier=pwb,
            scheduler_exit=se_dict,
            windup_guard_observation=wg_dict,
            query=query,
            acquisition_profile=_acq_profile,
            feed_cap_reason=_feed_cap_reason,
            nonfeed_priority_enabled=_nonfeed_priority_enabled,
            nonfeed_profile_expected_lanes=_nonfeed_profile_expected_lanes,
            # PUBLIC
            public_terminal_stage=r.public_terminal_stage,
            public_stage_counters=r.public_stage_counters,
            public_discovery_empty_reason=r.public_discovery_empty_reason,
            public_discovery_debug_reason=r.public_discovery_debug_reason,
            public_provider_selection_debug=r.public_provider_selection_debug or {},
            # CT
            ct_provider_status=r.ct_provider_status,
            ct_cache_used=r.ct_cache_used,
            ct_cache_stale=r.ct_cache_stale,
            ct_cache_age_s=r.ct_cache_age_s,
            ct_quarantine_count=r.ct_quarantine_count,
            ct_quarantine_samples=r.ct_quarantine_samples,
            ct_planned=r.ct_planned,
            ct_scheduled=r.ct_scheduled,
            ct_provider_selected=r.ct_provider_selected,
            ct_request_attempted=r.ct_request_attempted,
            ct_request_timeout=r.ct_request_timeout,
            ct_raw_count=r.ct_raw_count,
            ct_bridge_invoked=r.ct_bridge_invoked,
            ct_candidates_built=r.ct_candidates_built,
            ct_storage_attempted=r.ct_storage_attempted,
            ct_storage_accepted=r.ct_storage_accepted,
            ct_terminal_stage=r.ct_terminal_stage,
            ct_prelude_missing_but_final_attempted=r.ct_prelude_missing_but_final_attempted,
            # Rejection ledgers
            quality_rejection_summary_by_family=r.quality_rejection_summary_by_family,
            duplicate_rejection_summary_by_family=r.duplicate_rejection_summary_by_family,
            low_information_by_family=r.low_information_by_family,
            nonfeed_candidate_ledger_summary=r.nonfeed_candidate_ledger_summary,
            feed_dominance_budget=getattr(plan, "feed_dominance_budget", None) if plan else None,
            # DOH
            doh_planned=r.doh_planned,
            doh_scheduled=r.doh_scheduled,
            doh_request_attempted=r.doh_request_attempted,
            doh_domains_attempted=r.doh_domains_attempted,
            doh_raw_count=r.doh_raw_count,
            doh_accepted_findings=r.doh_accepted_findings,
            doh_terminal_stage=r.doh_terminal_stage,
            doh_provider_errors=r.doh_provider_errors,
            doh_cache_used=r.doh_cache_used,
            # Nonfeed surface
            nonfeed_expected_lanes=r.nonfeed_expected_lanes,
            nonfeed_missing_expected_lanes=r.nonfeed_missing_expected_lanes,
            wayback_terminal_state=r.wayback_terminal_state,
            passive_dns_terminal_state=r.passive_dns_terminal_state,
            nonfeed_surface_complete=getattr(result, "nonfeed_surface_complete", False),
            # Pivot seeds
            pivot_seed_domains=r.pivot_seed_domains,
            pivot_seed_ips=r.pivot_seed_ips,
            pivot_seed_urls=r.pivot_seed_urls,
            pivot_seed_hashes=r.pivot_seed_hashes,
            pivot_seed_cves=r.pivot_seed_cves,
            seed_context_available=bool(
                r.pivot_seed_domains
                or r.pivot_seed_ips
                or r.pivot_seed_urls
                or r.pivot_seed_hashes
                or r.pivot_seed_cves
            ),
            seed_context_propagated=r.seed_context_propagated,
            lanes_unlocked_by_seed_context=r.lanes_unlocked_by_seed_context,
            # Acquisition plan
            acquisition_plan_build_failed=r.acquisition_plan_build_failed,
            acquisition_plan_build_error_type=r.acquisition_plan_build_error_type,
            acquisition_plan_build_error=r.acquisition_plan_build_error,
            acquisition_plan_present_for_prelude=r.acquisition_plan_present_for_prelude,
            acquisition_plan_lanes_for_prelude=r.acquisition_plan_lanes_for_prelude,
            acquisition_plan_enabled_lanes_for_prelude=r.acquisition_plan_enabled_lanes_for_prelude,
            acquisition_plan_profile_for_prelude=r.acquisition_plan_profile_for_prelude,
            acquisition_plan_build_error_for_prelude=r.acquisition_plan_build_error_for_prelude,
            # Nonfeed prelude
            nonfeed_prelude_enabled=r.nonfeed_prelude_enabled,
            nonfeed_prelude_expected_lanes=r.nonfeed_prelude_expected_lanes,
            nonfeed_prelude_attempted_lanes=r.nonfeed_prelude_attempted_lanes,
            nonfeed_prelude_terminal_lanes=r.nonfeed_prelude_terminal_lanes,
            nonfeed_prelude_missing_lanes=r.nonfeed_prelude_missing_lanes,
            nonfeed_prelude_error_by_lane=r.nonfeed_prelude_error_by_lane,
            nonfeed_prelude_accepted_by_lane=r.nonfeed_prelude_accepted_by_lane,
            nonfeed_prelude_duration_s=r.nonfeed_prelude_duration_s,
            nonfeed_prelude_feed_blocked_until_complete=r.nonfeed_prelude_feed_blocked_until_complete,
        )
        # Post-processing
        # [ISSUE-007] Avoid unnecessary list() wrapping — fields are already correct type
        _acq_report["acquisition_profile_input"] = None
        _acq_report["acquisition_profile_effective"] = acq_effective
        _acq_report["acquisition_profile_normalized"] = False
        _acq_report["budget_violations"] = r.budget_violations
        _acq_report["return_guard_block_reason"] = r.return_guard_block_reason or ""
        _acq_report["ct_quarantine_count"] = r.ct_quarantine_count
        _acq_report["ct_quarantine_samples"] = r.ct_quarantine_samples
        _acq_report = reconcile_lane_detail_fields(_acq_report)
        _acq_report = complete_source_family_outcomes_from_lane_details(_acq_report)
        _acq_report = complete_source_family_outcomes_from_prelude(_acq_report)
        # Sprint F350M-R: Extract seed context normalization (clone elimination)
        _acq_report = _normalize_seed_context(_acq_report, r)

    except Exception as _exc:
        logger.exception(
            "[Issue9-FALLBACK] build_acquisition_report raised: %s",
            _exc,
        )
        # [Issue #9] Schema-driven fallback: msgspec.to_builtins(r) gives the same
        # 80-field structure that build_acquisition_report() would return — but
        # without retyping every field.  Then overlay only the 4 fallback-specific
        # overrides and fall through to the shared post-processing pipeline.
        # [ISSUE-011] Use msgspec.to_builtins() — returns plain dicts/lists without
        # msgspec node wrappers, reducing memory footprint on M1 8GB.
        _acq_report = msgspec.to_builtins(r)
        _acq_report.update(
            schema_version=f"{ACQUISITION_REPORT_SCHEMA_VERSION}-fallback",
            fallback_reason=f"canonical_build_failed: {_exc}",
            acquisition_report_fallback_used=True,
            terminality=term_rep,
            source_family_outcomes=sfo_list,
            return_guard=rg_dict,
            prewindup_barrier=pwb,
            scheduler_exit=se_dict,
            windup_guard_observation=wg_dict,
            nonfeed_plan_debug=nd,
            plan=getattr(plan, "plans", None) if plan else None,
            prelude_plan=getattr(plan, "plans", []) if plan else [],
            required_lane_plan=term_rep.get("required_lanes", []) if term_rep else [],
            runtime_attempted_lanes=[
                o.family for o in sfo_list if o.attempted and o.family
            ],
            effective_acquisition_plan=list(
                set(term_rep.get("required_lanes", []) if term_rep else [])
                | {o.family for o in sfo_list if o.attempted and o.family}
            ),
            plan_semantics=("effective_runtime" if any(o.attempted for o in sfo_list) else "prelude_only"),
        )
        # ── 9b. Fallback path: post-processing (mirrors success path lines 1046-1072) ─
        # [ISSUE-007] Avoid unnecessary list() wrapping — fields are already correct type
        _acq_report["acquisition_profile_input"] = None
        _acq_report["acquisition_profile_effective"] = acq_effective
        _acq_report["acquisition_profile_normalized"] = False
        _acq_report["budget_violations"] = r.budget_violations
        _acq_report["return_guard_block_reason"] = r.return_guard_block_reason or ""
        _acq_report["ct_quarantine_count"] = r.ct_quarantine_count
        _acq_report["ct_quarantine_samples"] = r.ct_quarantine_samples
        _acq_report = reconcile_lane_detail_fields(_acq_report)
        _acq_report = complete_source_family_outcomes_from_lane_details(_acq_report)
        _acq_report = complete_source_family_outcomes_from_prelude(_acq_report)
        # Sprint F350M-R: Extract seed context normalization (clone elimination)
        _acq_report = _normalize_seed_context(_acq_report, r)

    # ── 10. Return canonical wrapper ─────────────────────────────────────────────
    return {
        "acquisition_report": _acq_report,
        "acquisition_terminality_checked": r.acquisition_terminality_checked,
        "acquisition_terminality_satisfied": r.acquisition_terminality_satisfied,
        "acquisition_terminality_missing_lanes": r.acquisition_terminality_missing_lanes,
        "acquisition_terminality_report": term_rep,
        "source_family_outcomes": sfo_list,
        "scheduler_exit": se_dict,
        "return_guard": rg_dict,
        "windup_guard_observation": wg_dict,
        "prewindup_barrier": pwb,
        "acquisition_prelude_checked": r.acquisition_prelude_checked,
        "acquisition_prelude_ran": r.acquisition_prelude_ran,
        "acquisition_prelude_required_lanes": r.acquisition_prelude_required_lanes,
        "acquisition_prelude_terminal_lanes": r.acquisition_prelude_terminal_lanes,
        "acquisition_prelude_missing_lanes": r.acquisition_prelude_missing_lanes,
        "acquisition_prelude_skipped_lanes": r.acquisition_prelude_skipped_lanes,
        "acquisition_prelude_errors": r.acquisition_prelude_errors,
        "acquisition_prelude_duration_s": r.acquisition_prelude_duration_s,
        "acquisition_prelude_reason": r.acquisition_prelude_reason,
        "early_exit_class": r.early_exit_class,
        "early_exit_reason": r.early_exit_reason,
        "requested_duration_s": r.requested_duration_s,
        "actual_duration_s": r.actual_duration_s,
        "elapsed_pct": r.elapsed_pct,
        "active_window_budget_s": r.active_window_budget_s,
        "active_window_elapsed_s": r.active_window_elapsed_s,
    }


# =============================================================================
# Backward-compatibility alias — _scheduler_result_acquisition_payload now delegates
# to the schema-driven implementation. The old function body is preserved below
# for reference until Issue #9 is fully validated.
# =============================================================================


def _scheduler_result_acquisition_payload(
    result: Any,
    scheduler: Any,
    query: str,
    duration_s: float,
) -> dict[str, Any]:
    """
    [DEPRECATED — Issue #9] Legacy wrapper.

    Delegates to acq_payload_to_dict() which uses the schema-driven approach.
    Kept for zero-risk migration — swap call sites after validation.
    """
    return acq_payload_to_dict(result, scheduler, query, duration_s)


def _runtime_truth(inp: RuntimeTruthInput) -> dict:
    """Build canonical runtime-truth record from scheduler result data."""
    is_meaningful, evidence_note = _is_meaningful_run(
        inp.actual_duration_s,
        inp.cycles_completed,
        inp.cycles_started,
        inp.accepted_findings,
        inp.total_pattern_hits,
        swap_detected=inp.swap_detected,
        uma_state=inp.uma_state,
    )

    # Branch mix — dominant signal source
    # Sprint F194A: CT findings tracked as distinct branch in branch_mix
    branch_mix = {
        "feed_findings": inp.feed_findings,
        "public_findings": inp.public_accepted_findings,
        "ct_findings": inp.ct_findings,
    }

    # Primary signal source label — Sprint F194A: CT findings can dominate
    if inp.ct_findings > 0 and inp.feed_findings == 0 and inp.public_accepted_findings == 0:
        primary = "ct"
    elif inp.feed_findings > 0 and inp.public_accepted_findings == 0 and inp.ct_findings == 0:
        primary = "feed"
    elif inp.public_accepted_findings > 0 and inp.feed_findings == 0 and inp.ct_findings == 0:
        primary = "public"
    elif inp.feed_findings > 0 and inp.public_accepted_findings > 0 and inp.ct_findings == 0:
        # F214-ACQ: When feed dominates (>95%) and non-feed is minimal, label as feed
        # not mixed — the signal is overwhelmingly from the feed lane.
        total_nonfeed = inp.public_accepted_findings + inp.ct_findings
        feed_dominance_ratio = (
            inp.feed_findings / (inp.feed_findings + total_nonfeed) if (inp.feed_findings + total_nonfeed) > 0 else 1.0
        )  # noqa: E501
        if feed_dominance_ratio > 0.95:
            primary = "feed"
        else:
            primary = "mixed"
    elif inp.ct_findings > 0 and (inp.feed_findings > 0 or inp.public_accepted_findings > 0):
        primary = "mixed_ct"
    else:
        primary = "none"

    return {
        "is_meaningful": is_meaningful,
        "evidence_note": evidence_note,
        "command_params": {
            "query": inp.query,
            "requested_duration_s": inp.duration_s,
        },
        "actual_duration_s": round(inp.actual_duration_s, 2),
        "cycles_completed": inp.cycles_completed,
        "cycles_started": inp.cycles_started,
        "branch_mix": branch_mix,
        "primary_signal_source": primary,
        "total_pattern_hits": inp.total_pattern_hits,
        "accepted_findings": inp.accepted_findings,
        # F176A: Hardware pressure surfaces for smoke classification
        "pre_sprint_swap_detected": inp.swap_detected,
        "pre_sprint_uma_state": inp.uma_state,
        # Sprint F195B: Branch timeout telemetry
        "branch_timeout_count": inp.branch_timeout_count,
        "public_branch_timed_out": inp.public_branch_timed_out,
        "ct_branch_timed_out": inp.ct_branch_timed_out,
    }


def _get_live_feed_urls() -> list[str]:
    """
    Return canonical runtime feed URLs for live sprint path.

    Uses get_runtime_feed_seeds() from rss_atom_adapter — the single source
    of truth for the runtime RSS/Atom feed surface. Returns only ``curated_seed``
    entries sorted by priority descending. This is the accessor the canonical
    sprint owner path should use; topology_candidates are excluded by design.
    """
    from hledac.universal.discovery.rss_atom_adapter import get_runtime_feed_seeds

    return [seed.feed_url for seed in get_runtime_feed_seeds()]


# =============================================================================
# Pre-sprint checks
# =============================================================================


def _run_sprint_preflight_guards(
    logger: logging.Logger,
    duration_s: float,
    windup_lead_s: float | None,
    flags: "SprintFlags | None",
    force: bool,
    early_config: "SprintSchedulerConfig",
) -> tuple[float, "UMAStatus"]:
    """
    F360: Extracted pre-flight guard checks from run_sprint().

    Performs all config validation that MUST run before DuckDB init to avoid
    orphaned lock files. Uses sys.exit(2) for config errors.

    Returns:
        tuple of (effective_windup_s, uma_pre_sprint)
    """
    from hledac.universal.core.resource_governor import sample_uma_status

    # F221-ABORT: Pre-flight guard — enforce minimum active-window budget.
    # MUST run BEFORE DuckDB init to avoid orphaned lock files when the config
    # is rejected up front. Uses SprintSchedulerConfig internally to compute
    # effective_windup_lead_s so the guard and the scheduler are always in sync.
    # sys.exit(2) = config error, distinguishable from exit(1) runtime failure.
    _force_override = (flags.force if flags else False) or force
    _effective_windup_s = early_config.effective_windup_lead_s
    _active_window_s = float(duration_s) - _effective_windup_s

    # Sprint F271C: fail-loud invariant — if we somehow compute a negative
    # active window, the guard is broken; surface it as a config error
    # (exit 2) instead of proceeding with a bogus budget that hides bugs.
    if _active_window_s < 0.0:
        logger.error(
            "[F271C-INVARIANT] computed negative active_window_s=%s "
            "(duration_s=%s, effective_windup_s=%s). F221 guard is broken.",
            _active_window_s,
            duration_s,
            _effective_windup_s,
        )
        sys.exit(2)

    # F289-WINDUP: Sanity check — abort if windup consumes >= 80% of active window.
    if _effective_windup_s >= _active_window_s * 0.80:
        _pct = (_effective_windup_s / _active_window_s * 100) if _active_window_s > 0 else 100.0
        if _force_override:
            logger.warning(
                "[F289-FORCED] Windup %.0fs would consume %.0f%% of active window %.0fs. Proceeding due to --force.",
                _effective_windup_s,
                _pct,
                _active_window_s,
            )
        else:
            logger.error(
                "[F289-ABORT] Windup %.0fs would consume %.0f%% of active window %.0fs. "
                "Reduce windup (--windup-lead) or increase duration.",
                _effective_windup_s,
                _pct,
                _active_window_s,
            )
            sys.exit(2)

    if _active_window_s < float(MIN_ACTIVE_WINDOW_S):
        _required_duration_s = int(_effective_windup_s + float(MIN_ACTIVE_WINDOW_S))
        if _force_override:
            logger.warning(
                "[F221-FORCED] duration=%ds gives only %ds active window "
                "(windup_lead_effective=%ds). Proceeding due to --force.",
                int(duration_s),
                max(0, int(_active_window_s)),
                int(_effective_windup_s),
            )
        else:
            logger.error(
                "[F221-ABORT] Sprint duration %ds gives only %ds active window "
                "(windup_lead_effective=%ds). "
                "Minimum recommended: --duration %d. "
                "Use --force to override.",
                int(duration_s),
                max(0, int(_active_window_s)),
                int(_effective_windup_s),
                _required_duration_s,
            )
            sys.exit(2)  # exit(2) = config error, distinguishable from exit(1) runtime

    # F289: windup_lead_s sanity check — warn if it would consume >90% of sprint
    if windup_lead_s is not None:
        _windup_fraction = float(windup_lead_s) / float(duration_s)
        if _windup_fraction > 0.90:
            logger.warning(
                "[F289-WINDUP-FRACTION] windup_lead_s=%.0fs is %.0f%% of duration=%ds. "
                "This may cause the sprint to enter WINDUP immediately, leaving "
                "almost no time for active acquisition. Consider reducing windup_lead_s "
                "or increasing sprint duration.",
                int(windup_lead_s),
                _windup_fraction * 100,
                int(duration_s),
            )

    # F214Q: Remote debug OPSEC guard — strict exit if HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1
    # and PYTHON_DISABLE_REMOTE_DEBUG is not set. Python 3.14 activates safe-external-debugger by default.
    if FeatureFlags.get(FeatureFlag.REMOTE_DEBUG_DISABLE):
        if os.environ.get("PYTHON_DISABLE_REMOTE_DEBUG") != "1":
            sys.exit(
                "HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1 but PYTHON_DISABLE_REMOTE_DEBUG not set — "
                "OSINT runtime requires external debugger disabled"
            )

    # F176A: Pre-sprint UMA state capture — hardware pressure before scheduler runs.
    _uma_pre_sprint = sample_uma_status()

    return (_effective_windup_s, _uma_pre_sprint)


# Sprint M218A: GC startup tuning for M1 UMA stability.
# PHYSICS-06/07: Now delegates to BlitzGCStrategy (canonical GC lifecycle).
# _configure_gc_for_sprint() is a thin compatibility wrapper — the real work
# happens in BlitzGCStrategy.sprint_start() called from run_sprint().
# Opt-out via HLEDAC_DISABLE_GC_FREEZE=1.
_gc_configured: bool = False


def _configure_gc_for_sprint() -> dict:
    """
    Configure Python GC for sprint workload.

    PHYSICS-06/07: Compatibility wrapper — delegates to BlitzGCStrategy.
    The canonical GC lifecycle is managed by BlitzGCStrategy:
      - sprint_start() disables GC during active acquisition
      - sprint_teardown() re-enables GC at winddown

    Called once at sprint boot. Returns a dict with telemetry fields.
    Opt-out via HLEDAC_DISABLE_GC_FREEZE=1.
    """
    global _gc_configured
    if _gc_configured:
        return {"delegated_to": "BlitzGCStrategy", "already_configured": True}

    # PHYSICS-06/07: Delegate to BlitzGCStrategy (canonical path).
    # sprint_start() is called from run_sprint() right before acquisition;
    # this wrapper exists for backward-compat callers.
    try:
        from hledac.universal.coordinators.resource.blitz_gc import blitz_gc as _bgc

        _result = _bgc.sprint_start()
        _gc_configured = True
        return {
            "delegated_to": "BlitzGCStrategy",
            "blitz_active": _result.get("blitz_active", False),
            "freeze_method": _result.get("freeze_method", "none"),
            "blitz_thresholds": _result.get("blitz_thresholds"),
            "startup_snapshot_count": _result.get("startup_snapshot_count", 0),
        }
    except Exception as _exc:
        logger.debug("[GC] BlitzGCStrategy delegation failed (non-fatal): %s", _exc)
        _gc_configured = True
        return {"delegated_to": "BlitzGCStrategy", "error": str(_exc)}


def run_pre_sprint_checks() -> bool:
    """
    Run mandatory pre-sprint checks.

    Returns True if safe to proceed, False to abort.
    """
    checks_passed = True

    # F273G: macOS malloc pressure relief — release fragmented pages before any allocation.
    # Must run FIRST, before MLX buffers or any memory-heavy init.
    # F350M-R: Run malloc relief concurrently with UMA status via asyncio.TaskGroup.
    # Both are sync (CPU-bound) but asyncio.to_thread releases the GIL during execution.
    # Combined: ~same wall-clock as malloc alone, ~50% faster than sequential.
    _malloc_released = None

    # FIX: Refactored to eliminate nonlocal pattern and nested function anti-pattern.
    # Uses result containers passed as parameters instead of nonlocal closure capture.
    try:
        import asyncio as _asyncio

        _malloc_released, _uma_status = _asyncio.run(_run_concurrent_checks_async())
    except ExceptionGroup:
        # Partial failure — malloc may have failed, but continue
        _uma_status = sample_uma_status()
        logger.debug("[BOOT] Concurrent pre-flight partial failure, fell back to sequential")

    # Log malloc result if obtained
    if _malloc_released is not None and _malloc_released > 0:
        logger.debug("[BOOT] malloc_zone_pressure_relief released %d bytes", _malloc_released)
    # fail-soft: malloc_zone_pressure_relief unavailable

    # MLX wired limit — fail-soft (Sprint F207D)
    # MLX is optional. Skip Metal limit config when unavailable.
    # Sprint F500I: Lazy import — mlx_cache triggers 23s import of mlx_embeddings
    from hledac.universal.utils import mlx_cache

    if not mlx_cache.MLX_AVAILABLE:
        logger.info("[BOOT] MLX unavailable — skipping Metal wired limit")
    else:
        try:
            mlx_cache.init_mlx_buffers()
            status = mlx_cache.get_metal_limits_status()

            logger.info(
                f"[BOOT] MLX buffers: cache={_format_mib(status['cache_limit_bytes'])} wired={_format_mib(status['wired_limit_bytes'])} configured={status['configured']}"  # noqa: E501
            )
        except Exception as exc:
            logger.warning(f"[BOOT] MLX buffer init failed: {exc}")

    # F278A: Swap tiered policy — WARNING for diagnostic tier, EXIT 2 for hard_block.
    # SSOT: core/resource_governor.py CLEAN_SWAP_MAX_GIB / DIAGNOSTIC_SWAP_MAX_GIB / HARD_BLOCK_SWAP_GIB
    s = _uma_status if _uma_status is not None else sample_uma_status()
    if s.swap_used_gib > HARD_BLOCK_SWAP_GIB:
        logger.error(
            "[BOOT] SWAP %.1fGB > %.1fGB — HARD_BLOCK (restart required). Exit 2.",
            s.swap_used_gib,
            HARD_BLOCK_SWAP_GIB,
        )
        sys.exit(2)
    elif s.swap_used_gib > CLEAN_SWAP_MAX_GIB:
        logger.warning(
            f"[BOOT] SWAP {s.swap_used_gib:.1f}GB > {CLEAN_SWAP_MAX_GIB:.1f}GB (diagnostic tier) — "
            f"doporučuji restart před long run"
        )

    # SWARM-010: Feature flag validation — single source of truth.
    # Validates: deprecated flags, implications, conflicts, RAM budget.
    # Runs at startup to catch configuration errors before sprint begins.
    try:
        from hledac.universal.core.feature_flags import FeatureFlag, FeatureFlags

        flag_errors, flag_warnings = FeatureFlags.validate()
        for err in flag_errors:
            logger.error("[SWARM-010] %s: %s", err.flag, err.message)
        for warn in flag_warnings:
            logger.warning("[SWARM-010] %s: %s", warn.flag, warn.message)
        if flag_errors:
            logger.error(
                "[SWARM-010] Flag validation failed (%d error(s)). "
                "Fix flags above or set --force to bypass.",
                len(flag_errors),
            )
            sys.exit(2)  # exit(2) = config/validation error
    except ImportError:
        logger.debug("[SWARM-010] FeatureFlags not available (skipping validation)")
    except Exception as _exc:
        logger.warning("[SWARM-010] Flag validation skipped due to error: %s", _exc)

    logger.info(f"[BOOT] Pre-sprint checks OK | UMA: {s.system_used_gib:.2f}GiB used | swap: {s.swap_used_gib:.2f}GiB")
    return checks_passed


# =============================================================================
# Sprint delta writer (uses existing DuckDB schema)
# =============================================================================


def _derive_top_source(hits_per_source: dict[str, int]) -> str:
    """Return source with most hits, or empty string if no data."""
    if not hits_per_source:
        return ""
    return max(hits_per_source, key=lambda k: hits_per_source[k])


def _format_mib(value: int | None) -> str:
    """Format bytes as MiB string, or 'N/A' if value is None/0."""
    return f"{value // (1024 * 1024):.0f}MiB" if value else "N/A"


def _extract_nonfeed_debug_fields(nd_raw: Any | None) -> dict[str, Any] | None:
    """Extract all nonfeed_plan_debug fields safely — single getattr chain.

    Replaces 14 individual getattr calls with one helper. nd_raw is a mutable
    diagnostic snapshot that may not have all fields defined (ISSUE-016).

    Args:
        nd_raw: NonfeedPlanDebug instance or None.

    Returns:
        Dict with all nonfeed debug fields, or None if nd_raw is None.
    """
    if nd_raw is None:
        return None
    # Single getattr chain for all fields
    return {
        "domain_detected": getattr(nd_raw, "domain_detected", False),
        "wallet_detected": getattr(nd_raw, "wallet_detected", False),
        "enabled_nonfeed_lanes": getattr(nd_raw, "enabled_nonfeed_lanes", ()) or (),
        "disabled_nonfeed_lanes": getattr(nd_raw, "disabled_nonfeed_lanes", ()) or (),
        "disabled_reasons": getattr(nd_raw, "disabled_reasons", ()) or (),
        "scheduled_nonfeed_lanes": getattr(nd_raw, "scheduled_nonfeed_lanes", ()) or (),
        "hardware_skipped_lanes": getattr(nd_raw, "hardware_skipped_lanes", ()) or (),
        "nonfeed_execution_scheduled": getattr(nd_raw, "nonfeed_execution_scheduled", False),
        "nonfeed_execution_skip_reason": getattr(nd_raw, "nonfeed_execution_skip_reason", None),
        "acquisition_profile": getattr(nd_raw, "acquisition_profile", "default"),
        "feed_cap_reason": getattr(nd_raw, "feed_cap_reason", None),
        "nonfeed_priority_enabled": getattr(nd_raw, "nonfeed_priority_enabled", False),
        "nonfeed_profile_expected_lanes": getattr(nd_raw, "nonfeed_profile_expected_lanes", ()) or (),
    }


async def write_sprint_delta(
    store: "DuckDBShadowStore",
    sprint_id: str,
    query: str,
    new_findings: int,
    dedup_hits: int,
    ioc_nodes: int,
    uma_baseline_gib: float,
    uma_peak_gib: float,
    synthesis_success: bool,
    duration_s: float,
    hits_per_source: dict[str, int],
    seed_state: Any = None,  # ULTIMATE-001: SprintSeedState for deterministic replay
) -> None:
    """Write sprint_delta record to DuckDB at TEARDOWN."""
    try:
        findings_per_min = (new_findings / (duration_s / 60.0)) if duration_s > 0 else 0.0
        top_source = _derive_top_source(hits_per_source)
        row = {
            "sprint_id": sprint_id,
            "ts": time.time(),
            "query": query,
            "duration_s": duration_s,
            "new_findings": new_findings,
            "dedup_hits": dedup_hits,
            "ioc_nodes": ioc_nodes,
            "ioc_new_this_sprint": new_findings,
            "uma_peak_gib": uma_peak_gib - uma_baseline_gib,
            "synthesis_success": synthesis_success,
            "findings_per_minute": findings_per_min,
            "top_source_type": top_source,
            "synthesis_confidence": 1.0 if synthesis_success else 0.0,
        }
        # ULTIMATE-001: Include seed state fields for deterministic replay
        if seed_state is not None:
            row["prng_seed"] = seed_state.prng_seed
            row["tot_iv"] = seed_state.tot_iv
            row["config_hash"] = seed_state.config_hash
            row["seed_created_at"] = seed_state.created_at
        # Wait for store to be ready — ISSUE-006: event-driven wait, no polling
        # ISSUE-006-EXT: 20s timeout for canonical write path (slow disk tolerance)
        if not await store.wait_until_ready(timeout_s=20.0):
            logger.info("[TEARDOWN] DuckDB store not ready after 20s timeout — recording anyway (fail-safe)")
        await store.async_record_sprint_delta(row)
        logger.info(
            f"[TEARDOWN] sprint_delta written: {new_findings} findings, "
            f"{dedup_hits} dedup hits, "
            f"UMA delta: {uma_peak_gib - uma_baseline_gib:+.2f}GiB, "
            f"top_source: {top_source!r}, "
            f"findings_per_min: {findings_per_min:.2f}"
        )
    except Exception as exc:
        logger.warning(f"[TEARDOWN] sprint_delta write failed: {exc}")


# =============================================================================
# Dry-run diagnostic — pre-sprint validation without discovery
# =============================================================================


# ── Dry-run helper functions ────────────────────────────────────────────────────


def _validate_timing_config(duration_s: float) -> tuple[float, float, list[str], str]:
    """Validate timing config and return (effective_windup, active_budget, issues, verdict)."""
    issues: list[str] = []
    verdict = "OK"
    _WINDUP_MIN = 30.0
    _WINDUP_MAX = 180.0
    _WINDUP_RATIO = 0.30
    effective_windup = max(_WINDUP_MIN, min(_WINDUP_MAX, duration_s * _WINDUP_RATIO))
    active_budget = max(0.0, duration_s - effective_windup)
    if effective_windup >= duration_s:
        issues.append(f"windup_lead ({effective_windup:.0f}s) >= duration ({duration_s:.0f}s) — windup would never end")
        verdict = "ABORT_RECOMMENDED"
    elif active_budget <= 0:
        issues.append(f"active_budget ({active_budget:.0f}s) <= 0 for {duration_s:.0f}s sprint — no room for fetch")
        verdict = "ABORT_RECOMMENDED"
    try:
        if float(duration_s) <= 0:
            raise ValueError("duration must be positive")
    except (TypeError, ValueError):
        issues.append(f"duration ({duration_s}) is not a valid positive float")
        verdict = "ABORT_RECOMMENDED"
    return effective_windup, active_budget, issues, verdict


def _check_hermes_availability() -> tuple[bool, list[str], str]:
    """Check Hermes3 model availability. Returns (ok, issues, verdict)."""
    issues: list[str] = []
    verdict = "OK"
    hermes_ok = False
    try:
        from hledac.universal.brain.model_lifecycle import get_model_lifecycle_status
        status_dict = get_model_lifecycle_status()
        hermes_ok = status_dict.get("loaded", False)
    except Exception as e:
        issues.append(f"Hermes3 model_lifecycle check failed: {e}")
    if not hermes_ok:
        issues.append("Hermes3 model not loaded — synthesis will be skipped")
        if verdict == "OK":
            verdict = "OK_WITH_WARNINGS"
    return hermes_ok, issues, verdict


def _check_uma_snapshot() -> tuple[float, str, list[str], str]:
    """Check UMA memory snapshot. Returns (uma_gib, uma_state, issues, verdict)."""
    issues: list[str] = []
    verdict = "OK"
    uma_gib = 0.0
    uma_state = "unknown"
    try:
        uma = sample_uma_status()
        uma_gib = getattr(uma, "system_available_gib", 0.0)
        uma_state = getattr(uma, "state", "unknown")
    except Exception as e:
        issues.append(f"UMA snapshot failed: {e}")
        verdict = "ABORT_RECOMMENDED"
    if uma_gib < 1.0:
        issues.append(f"UMA available < 1 GiB ({uma_gib:.1f}) — Hermes3 load may OOM on M1 8GB")
        if verdict == "OK":
            verdict = "OK_WITH_WARNINGS"
    if uma_state == "emergency":
        issues.append("UMA state=emergency — abort recommended")
        verdict = "ABORT_RECOMMENDED"
    return round(uma_gib, 2), uma_state, issues, verdict


async def _probe_dns(target_host: str) -> tuple[dict | None, list[str], str]:
    """Probe DNS for target host. Returns (dns_result, issues, verdict)."""
    import socket
    issues: list[str] = []
    verdict = "OK"
    dns_result: dict | None = None
    try:
        await safe_wait_for(
            asyncio.to_thread(socket.gethostbyname, target_host),
            timeout=5.0,
            label="dns_resolve",
        )
        dns_result = {"target": target_host, "status": "ok"}
    except (TimeoutError, socket.gaierror) as e:
        issues.append(f"DNS resolve failed for '{target_host}': {e}")
        if verdict == "OK":
            verdict = "OK_WITH_WARNINGS"
    except Exception as e:
        issues.append(f"Network probe failed: {e}")
    return dns_result, issues, verdict


async def _check_source_availability() -> tuple[dict[str, bool], list[str], str]:
    """Check source availability (crt.sh, CIRCL PDNS). Returns (online_sources, issues, verdict)."""
    issues: list[str] = []
    verdict = "OK"
    online_sources: dict[str, bool] = {"crt.sh": False, "circl_pdns": False}
    src_checks = [
        ("crt.sh", "https://crt.sh/?q=%.example.com"),
        ("circl_pdns", "https://cirolve.circl.lu/api/pdns?q=example.com"),
    ]
    try:
        from hledac.universal.transport.session_pool import session_pool
        session = await session_pool.httpx()

        async def check_source(name: str, url: str) -> tuple[str, bool]:
            resp = await session.head(url)
            return (name, resp.status_code < 500)

        result = await parallel(
            [check_source(name, url) for name, url in src_checks],
            policy="collect",
            ctx="source_availability",
        )
        for name, ok in result.ok:
            online_sources[name] = ok
    except Exception:  # noqa: BLE001
        pass
    for src, ok in online_sources.items():
        if not ok:
            issues.append(f"{src} unreachable — may be skipped at runtime")
            if verdict == "OK":
                verdict = "OK_WITH_WARNINGS"
    return online_sources, issues, verdict


def _build_timing_plan(duration_s: float, effective_windup: float, active_budget: float) -> dict:
    """Build sprint timing plan structure."""
    return {
        "duration": duration_s,
        "windup_lead": effective_windup,
        "active_budget": active_budget,
        "phases": [
            {"phase": "WINDUP", "t_start": 0.0, "t_end": effective_windup, "description": "seed, bootstrap"},
            {"phase": "ACTIVE", "t_start": effective_windup, "t_end": effective_windup + active_budget, "description": f"{active_budget:.0f}s available for fetch"},
            {"phase": "SYNTHESIS", "t_start": effective_windup + active_budget, "t_end": duration_s, "description": "synthesis + export budget"},
        ],
    }


async def dry_run_sprint(query: str, duration_s: float = 300.0) -> None:
    """
    Dry-run mode: validate config, check Hermes/UMA/sources, show timing plan.
    Read-only — no DuckDB writes, no real discovery, no data downloads.
    """
    report: dict[str, Any] = {
        "target": query,
        "duration": duration_s,
        "windup_lead": 0.0,
        "active_budget": 0.0,
        "hermes_available": False,
        "uma_available_gib": 0.0,
        "sources_online": {},
        "issues": [],
        "verdict": "OK",
        "sprint_timing_plan": None,
    }
    # Collect all issues and track final verdict
    all_issues: list[str] = []
    verdict = "OK"
    # Merge helper results
    eff_windup, act_budget, timing_issues, timing_verdict = _validate_timing_config(duration_s)
    all_issues.extend(timing_issues)
    verdict = timing_verdict if timing_verdict != "OK" else verdict
    report["windup_lead"] = eff_windup
    report["active_budget"] = act_budget
    hermes_ok, hermes_issues, hermes_verdict = _check_hermes_availability()
    all_issues.extend(hermes_issues)
    report["hermes_available"] = hermes_ok
    if hermes_verdict != "OK":
        verdict = hermes_verdict
    uma_gib, uma_state, uma_issues, uma_verdict = _check_uma_snapshot()
    all_issues.extend(uma_issues)
    report["uma_available_gib"] = uma_gib
    report["uma_state"] = uma_state
    if uma_verdict != "OK":
        verdict = uma_verdict
    target_host = query.replace("https://", "").replace("http://", "").split("/")[0].split()[0]
    dns_result, dns_issues, dns_verdict = await _probe_dns(target_host)
    all_issues.extend(dns_issues)
    report["dns_resolve"] = dns_result
    if dns_verdict != "OK":
        verdict = dns_verdict
    online_sources, src_issues, src_verdict = await _check_source_availability()
    all_issues.extend(src_issues)
    report["sources_online"] = online_sources
    if src_verdict != "OK":
        verdict = src_verdict
    report["sprint_timing_plan"] = _build_timing_plan(duration_s, eff_windup, act_budget)
    report["issues"] = all_issues
    report["verdict"] = verdict
    _print_dry_run_summary(report)
    try:
        report_dir = Path.home() / ".hledac" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "DRY_RUN_REPORT.json"
        report_path.write_bytes(_serialize_report(report))
        logger.info(f"[DRY-RUN] Report written to {report_path}")
    except Exception as e:
        logger.warning(f"[DRY-RUN] Failed to write report: {e}")


def _print_dry_run_summary(report: dict) -> None:
    """Print human-readable dry-run summary to console."""
    plan = report.get("sprint_timing_plan") or {}
    phases = plan.get("phases", [])
    dur = report["duration"]

    print()
    print("=" * 60)
    print(f"  DRY-RUN: {report['target']!r}  ({dur:.0f}s)")
    print("=" * 60)
    print()

    if phases:
        print("  Sprint Timing Plan")
        print(f"  ─{'─' * 54}")
        for p in phases:
            t0 = p["t_start"]
            t1 = p["t_end"]
            print(f"  [T={t0:5.0f}s–{t1:5.0f}s]  {p['phase']:<10}  {p['description']}")
        print()
        print(
            f"  Active budget: {plan.get('active_budget', 0):.0f}s  │  UMA available: {report.get('uma_available_gib', 0):.1f} GiB"
        )  # noqa: E501
    print()
    print(f"  Hermes3:      {'✓ available' if report.get('hermes_available') else '✗ not loaded'}")
    sources = report.get("sources_online", {})
    dns = report.get("dns_resolve", {})
    print(f"  DNS probe:     {'✓ ' + dns.get('target', '') if dns.get('status') == 'ok' else '✗ failed'}")
    print(f"  crt.sh:       {'✓' if sources.get('crt.sh') else '✗ unreachable'}")
    print(f"  CIRCL PDNS:   {'✓' if sources.get('circl_pdns') else '✗ unreachable'}")
    print()
    issues = report.get("issues", [])
    if issues:
        print("  Issues:")
        for iss in issues:
            print(f"    ! {iss}")
        print()
    print(f"  Verdict: {report['verdict']}")
    print("=" * 60)
    print()


# =============================================================================
# Helper functions (extracted to reduce run_sprint complexity)
# =============================================================================


def _cleanup_stale_locks(lock_dir: Path, logger: logging.Logger) -> int:
    """
    Sprint F320: Stale-lock janitor.

    Scans lock_dir for *.lock files whose owning PID is dead.
    Removes stale locks and returns count of removed entries.

    Extracted from run_sprint to reduce nesting complexity (was depth 12-21).
    """
    removed_count = 0
    try:
        from hledac.universal.core.psutil_shim import psutil_module

        _ps = psutil_module()
        if _ps is None:
            return 0
        if not lock_dir.exists():
            return 0

        for lock_file in lock_dir.iterdir():
            if not lock_file.name.endswith(".lock"):
                continue
            try:
                pid_bytes = lock_file.read_bytes()
                if len(pid_bytes) >= 4:
                    lock_pid = int.from_bytes(pid_bytes[:4], byteorder="little")
                    if not _ps.pid_exists(lock_pid):
                        lock_file.unlink()
                        removed_count += 1
                        logger.info(
                            f"[F320-JANITOR] Removed stale lock: {lock_file.name} (PID={lock_pid} dead)"
                        )
            except Exception:  # noqa: BLE001
                pass  # best-effort
    except Exception:  # noqa: BLE001
        pass  # janitor failure is non-fatal
    return removed_count


# ── Verdict/Hint decision tables ────────────────────────────────────────────────


def _get_aborted_base_verdict(dup_rate: float, public_pct: float, feed_fnd: int) -> str:
    """Get verdict base for aborted sprint with partial results."""
    if dup_rate > 85:
        return "📦  NOISE-HEAVY: duplicated heavily"
    if public_pct > 60:
        return "🌐  PUBLIC-LED: public discovery dominated"
    if public_pct > 25:
        return "⚖️  MIXED: public contributed meaningfully"
    if feed_fnd > 0:
        return "✅  FEED-LED: feed sources strong"
    return "✅  SIGNAL: good feed performance"


# Decision table: (condition_fn, verdict_str) — first match wins
_VERDICT_TABLE: list[tuple[tuple, str]] = [
    # Aborted with findings
    (("aborted", True, "accepted_findings", lambda v: v > 0), "ABORTED_PARTIAL"),
    # Aborted without findings
    (("aborted", True), "ABORTED_HARD"),
    # Hardware/public issues
    (("hardware_limited", True), "HARDWARE_LIMITED"),
    (("public_backend_degraded", True), "DEGRADED"),
    # No findings cases
    (("accepted_findings", 0, "public_discovered", lambda v: v > 0), "NOVELTY"),
    (("accepted_findings", 0, "total_pattern_hits", 0), "DEPLETED"),
    (("accepted_findings", 0), "SILENT"),
    # Quality-based verdicts
    (("dup_rate", lambda v: v > 85), "NOISE_HEAVY"),
    (("public_pct", lambda v: v > 60), "PUBLIC_LED"),
    (("public_pct", lambda v: v > 25), "MIXED"),
    (("feed_fnd", lambda v: v > 0), "FEED_LED"),
]

_VERDICT_TEMPLATES: dict[str, str] = {
    "ABORTED_PARTIAL": "⚠️  ABORTED (partial) — {base}",
    "ABORTED_HARD": "⚠️  ABORTED: hard stop, no signal collected",
    "HARDWARE_LIMITED": "💾  HARDWARE-LIMITED: swap/memory pressure blocked entry",
    "DEGRADED": "🌐  DEGRADED: public backend/network error — check TOR/proxy/config",
    "NOVELTY": "🔍  NOVELTY: public found hits, feed accepted nothing",
    "DEPLETED": "🗿  DEPLETED: no pattern hits anywhere",
    "SILENT": "🤷  SILENT: pattern hits but no accepted findings",
    "NOISE_HEAVY": "📦  NOISE-HEAVY: duplicated heavily",
    "PUBLIC_LED": "🌐  PUBLIC-LED: public discovery dominated",
    "MIXED": "⚖️  MIXED: public contributed meaningfully",
    "FEED_LED": "✅  FEED-LED: feed sources strong",
    "SIGNAL": "✅  SIGNAL: good feed performance",
}

# Decision table for hints
_HINT_TABLE: list[tuple[tuple, str]] = [
    (("hardware_limited", True), "hardware memory pressure — free RAM or restart before next run"),
    (("accepted_findings", 0, "total_pattern_hits", 0), "query may be too narrow — broaden terms or switch seed"),
    (("dup_rate", lambda v: v > 80), "high dup rate — consider narrowing query scope"),
    (("public_pct", lambda v: v > 60), "public discovery effective — let it run longer next time"),
    (("public_pct", lambda v: v < 10, "feed_fnd", 0), "feed yield low — check if sources still alive (urlhaus, threatfox)"),
    (("public_pct", lambda v: v < 10, "feed_fnd", lambda v: v > 0), "feed performing — rely on feed-first, use public as supplemental"),
    (("public_discovered", lambda v: v > 0, "public_fetched", 0), "public discovered but not fetched — check network/TOR"),
    (("stop_requested", True), "early stop triggered — lower threshold or widen query"),
]


def _match_condition(value: Any, expected: Any) -> bool:
    """Match a condition value against expected (supports lambdas)."""
    if callable(expected):
        return expected(value)
    return value == expected


def _find_table_match(
    table: list[tuple[tuple, Any]],
    ctx: dict[str, Any],
    default: Any,
) -> Any:
    """
    Sprint F350M-R: Generic decision table matcher.
    
    Iterates through table rows of ((field, expected, field, expected, ...), result)
    and returns the first matching result, or default if no match.
    """
    for conditions, result in table:
        matched = True
        for i in range(0, len(conditions), 2):
            field = conditions[i]
            expected = conditions[i + 1]
            if not _match_condition(ctx[field], expected):
                matched = False
                break
        if matched:
            return result
    return default


@dataclass
class VerdictHintInput:
    """
    Sprint F350M-R: Input bundle for _compute_verdict_and_hint.
    
    Reduces function signature from 11 parameters to 1.
    """
    aborted: bool
    accepted_findings: int
    dup_rate: float
    public_pct: float
    feed_fnd: int
    hardware_limited: bool
    public_backend_degraded: bool
    public_discovered: int
    total_pattern_hits: int
    public_fetched: int
    stop_requested: bool
    
    def to_dict(self) -> dict:
        return {
            "aborted": self.aborted,
            "accepted_findings": self.accepted_findings,
            "dup_rate": self.dup_rate,
            "public_pct": self.public_pct,
            "feed_fnd": self.feed_fnd,
            "hardware_limited": self.hardware_limited,
            "public_backend_degraded": self.public_backend_degraded,
            "public_discovered": self.public_discovered,
            "total_pattern_hits": self.total_pattern_hits,
            "public_fetched": self.public_fetched,
            "stop_requested": self.stop_requested,
        }


@dataclass
class CheckpointInput:
    """
    Sprint F350M-R: Input bundle for _compute_checkpoint_priority and _compute_checkpoint_category.
    
    Reduces function signatures from 13/14 parameters to 1.
    """
    accepted_findings: int
    total_pattern_hits: int
    public_error: str | None
    public_discovered: int
    public_backend: bool
    feed_zero_check: bool
    cross_branch_fail_check: bool
    is_pre_active_mem_starved: bool
    is_hardware_limited: bool
    is_meaningful: bool
    uma_state_pre: str
    feed_fnd: int
    phase_times: dict


@dataclass
class RuntimeTruthInput:
    """
    Sprint F350M-R: Input bundle for _runtime_truth.
    
    Reduces function signature from 15 parameters to 1.
    """
    actual_duration_s: float
    query: str
    duration_s: float
    cycles_completed: int
    cycles_started: int
    accepted_findings: int
    total_pattern_hits: int
    public_accepted_findings: int
    feed_findings: int
    ct_findings: int = 0
    swap_detected: bool = False
    uma_state: str = "ok"
    branch_timeout_count: int = 0
    public_branch_timed_out: bool = False
    ct_branch_timed_out: bool = False


@dataclass
class ReportBuildInput:
    """
    Sprint F350M-R: Input bundle for _build_report_dict.
    
    Reduces function signature from 23 parameters to 1.
    Bundles context, result, metrics, and classifications computed in windup phase.
    """
    # Core identifiers
    query: str
    duration_s: float
    actual_duration: float
    
    # Pre-computed metrics
    feed_fnd: int
    dup_rate: float
    findings_per_min: float
    public_pct: float
    src_mix_str: str
    
    # Pre-computed classifications
    verdict: str
    next_hint: str
    phase_durations: dict
    runtime_truth: dict
    timing_truth: dict
    runtime_truth_level: str
    observed_run_tuple: tuple
    ckpt_category: str
    checkpoint_zero_reason: str
    export_finish_status: str
    uma_peak_gib: float  # Raw peak memory from sample_uma_status()
    
    # Context references
    ctx: "SprintRunContext"
    result: Any
    acq_payload_filtered: dict


@dataclass
class ExportHandoffInput:
    """
    Sprint F350M-R: Input bundle for _build_export_handoff.
    
    Reduces function signature from 17 parameters to 1.
    Bundles context, result, and pre-computed classifications.
    """
    # Core identifiers
    query: str
    duration_s: float
    actual_duration: float
    
    # Pre-computed classifications
    runtime_truth: dict
    timing_truth: dict
    runtime_truth_level: str
    observed_run_tuple: tuple
    src_mix_str: str
    ckpt_category: str
    checkpoint_zero_reason: str
    export_finish_status: str
    phase_durations: dict
    
    # Context references
    ctx: "SprintRunContext"
    result: Any
    top_seed_nodes: list
    live_feed_urls: list
    acq_payload: dict


def _compute_verdict_and_hint(inp: VerdictHintInput) -> tuple[str, str]:
    """
    Sprint F350M-R: Extracted verdict + next_hint heuristics using decision tables.

    Reduces run_sprint cyclomatic complexity by ~25 points.
    Pure function — no side effects, no external dependencies.
    """
    ctx = inp.to_dict()

    # Compute verdict using decision table
    verdict_key = _find_table_match(_VERDICT_TABLE, ctx, "SIGNAL")

    # Build verdict string
    if verdict_key == "ABORTED_PARTIAL":
        base = _get_aborted_base_verdict(dup_rate, public_pct, feed_fnd)
        verdict = _VERDICT_TEMPLATES["ABORTED_PARTIAL"].format(base=base)
    else:
        verdict = _VERDICT_TEMPLATES.get(verdict_key, "✅  SIGNAL: good feed performance")

    # Compute hint using decision table
    next_hint = _find_table_match(
        _HINT_TABLE, ctx, "current query and source mix working — continue as-is"
    )

    return verdict, next_hint


# =============================================================================
# Sprint F350M-R: Checkpoint category helpers — extracted for DRY refactor
# =============================================================================


class _CheckpointPriority:
    """
    Priority constants for checkpoint category branching.

    Used by _compute_checkpoint_priority() to determine which condition
    matched first in the checkpoint category chain.
    """

    SIGNAL_REACHES_FINDINGS = 1
    PRE_ACTIVE_MEMORY_STARVATION = 2
    SURVIVAL_ACTIVE_MINIMAL = 3
    HARDWARE_LIMITED_SMOKE = 4
    PUBLIC_BACKEND_DEGRADED = 5
    DEGRADED_PUBLIC_BLOCKER = 6
    MEANINGFUL_EMPTY_RUN = 7
    FEED_INGRESS_BLOCKER = 8
    FEED_SOURCE_INACCESSIBLE = 9
    SHORT_SIGNAL = 10
    TRUE_DEPLETED_QUERY = 11
    CROSS_BRANCH_SOURCE_INACCESSIBLE = 12
    WINDUP_EXPORT_FAIL_SOFT = 13
    DEPLETED = 14


# Mapping from priority → (category_name, reason_type)
# reason_type: "static" = use template as-is, "evidence_note" = use evidence_note arg,
#              "public_error" = format with public_error, "public_discovered" = format with public_discovered
_CHECKPOINT_PRIORITY_MAP: dict[int, tuple[str, str]] = {
    _CheckpointPriority.SIGNAL_REACHES_FINDINGS: ("signal_reaches_findings", "static"),
    _CheckpointPriority.PRE_ACTIVE_MEMORY_STARVATION: ("pre_active_memory_starvation", "static"),
    _CheckpointPriority.SURVIVAL_ACTIVE_MINIMAL: ("survival_active_minimal", "evidence_note"),
    _CheckpointPriority.HARDWARE_LIMITED_SMOKE: ("hardware_limited_smoke", "evidence_note"),
    _CheckpointPriority.PUBLIC_BACKEND_DEGRADED: ("public_backend_degraded", "public_error_degraded"),
    _CheckpointPriority.DEGRADED_PUBLIC_BLOCKER: ("degraded_public_blocker", "public_error_blocked"),
    _CheckpointPriority.MEANINGFUL_EMPTY_RUN: ("meaningful_empty_run", "static"),
    _CheckpointPriority.FEED_INGRESS_BLOCKER: ("feed_ingress_blocker", "public_discovered"),
    _CheckpointPriority.FEED_SOURCE_INACCESSIBLE: ("feed_source_inaccessible", "static"),
    _CheckpointPriority.SHORT_SIGNAL: ("short_signal", "static"),
    _CheckpointPriority.TRUE_DEPLETED_QUERY: ("true_depleted_query", "static"),
    _CheckpointPriority.CROSS_BRANCH_SOURCE_INACCESSIBLE: ("cross_branch_source_inaccessible", "static"),
    _CheckpointPriority.WINDUP_EXPORT_FAIL_SOFT: ("windup_export_fail_soft", "evidence_note"),
    _CheckpointPriority.DEPLETED: ("depleted", "static"),
}

# Static reason templates for static reason types
_CHECKPOINT_REASON_TEMPLATES: dict[int, str] = {
    _CheckpointPriority.SIGNAL_REACHES_FINDINGS: "signal_reaches_findings",
    _CheckpointPriority.PRE_ACTIVE_MEMORY_STARVATION: "pre_active_memory_starvation",
    _CheckpointPriority.MEANINGFUL_EMPTY_RUN: "meaningful_empty_run",
    _CheckpointPriority.FEED_SOURCE_INACCESSIBLE: "feed_source_inaccessible",
    _CheckpointPriority.SHORT_SIGNAL: "short_signal_no_findings",
    _CheckpointPriority.TRUE_DEPLETED_QUERY: "true_depleted_query:hits_without_acceptance",
    _CheckpointPriority.CROSS_BRANCH_SOURCE_INACCESSIBLE: "cross_branch_source_inaccessible",
    _CheckpointPriority.DEPLETED: "depleted_no_pattern_hits",
}


def _compute_checkpoint_priority(inp: CheckpointInput) -> int:
    """
    Sprint F350M-R: Compute checkpoint priority from conditions.

    Extracts the branching logic into a single function, eliminating
    duplicate condition evaluation between _ckpt_category and _checkpoint_zero_reason.

    Returns priority integer (lower = higher priority, checked first).
    """
    if inp.accepted_findings > 0:
        return _CheckpointPriority.SIGNAL_REACHES_FINDINGS
    if inp.is_pre_active_mem_starved:
        return _CheckpointPriority.PRE_ACTIVE_MEMORY_STARVATION
    if inp.is_meaningful and inp.uma_state_pre in ("warn", "critical", "emergency"):
        return _CheckpointPriority.SURVIVAL_ACTIVE_MINIMAL
    if inp.is_hardware_limited:
        return _CheckpointPriority.HARDWARE_LIMITED_SMOKE
    if inp.public_backend:
        return _CheckpointPriority.PUBLIC_BACKEND_DEGRADED
    if inp.public_error:
        return _CheckpointPriority.DEGRADED_PUBLIC_BLOCKER
    if inp.is_meaningful and inp.total_pattern_hits == 0 and inp.accepted_findings == 0:
        return _CheckpointPriority.MEANINGFUL_EMPTY_RUN
    if inp.feed_zero_check and inp.public_discovered > 0:
        return _CheckpointPriority.FEED_INGRESS_BLOCKER
    if inp.feed_zero_check and inp.total_pattern_hits == 0 and not inp.public_error:
        return _CheckpointPriority.FEED_SOURCE_INACCESSIBLE
    if inp.is_meaningful and inp.total_pattern_hits > 0 and inp.accepted_findings == 0:
        return _CheckpointPriority.SHORT_SIGNAL
    if inp.accepted_findings == 0 and inp.total_pattern_hits > 0 and not inp.public_backend:
        return _CheckpointPriority.TRUE_DEPLETED_QUERY
    if inp.cross_branch_fail_check:
        return _CheckpointPriority.CROSS_BRANCH_SOURCE_INACCESSIBLE
    if inp.accepted_findings == 0 and inp.phase_times.get("WINDUP", 0) > 0 and inp.is_meaningful:
        return _CheckpointPriority.WINDUP_EXPORT_FAIL_SOFT
    return _CheckpointPriority.DEPLETED


def _compute_checkpoint_category(inp: CheckpointInput, evidence_note: str) -> tuple[str, str]:
    """
    Sprint F350M-R: Extracted checkpoint category + reason taxonomy.

    Reduces run_sprint cyclomatic complexity by ~30 points.
    Pure function — no side effects, no external dependencies.

    Refactored: Condition branching extracted to _compute_checkpoint_priority(),
    eliminating duplicate ~28-line ternary chains.

    Bucket set:
      signal_reaches_findings, pre_active_memory_starvation, survival_active_minimal,
      hardware_limited_smoke, public_backend_degraded, degraded_public_blocker,
      meaningful_empty_run, feed_ingress_blocker, feed_source_inaccessible,
      true_depleted_query, short_signal, cross_branch_source_inaccessible,
      windup_export_fail_soft, depleted

    Returns (_ckpt_category: str, _checkpoint_zero_reason: str)
    """
    # Step 1: Compute priority once
    priority = _compute_checkpoint_priority(inp)

    # Step 2: Lookup category and reason type
    _ckpt_category, reason_type = _CHECKPOINT_PRIORITY_MAP[priority]

    # Step 3: Compute reason string based on type
    if reason_type == "static":
        _checkpoint_zero_reason = _CHECKPOINT_REASON_TEMPLATES[priority]
    elif reason_type == "evidence_note":
        _checkpoint_zero_reason = evidence_note if evidence_note else "unknown_checkpoint_reason"
    elif reason_type == "public_error_degraded":
        _checkpoint_zero_reason = f"public_backend_degraded:{public_error or ''}"
    elif reason_type == "public_error_blocked":
        _checkpoint_zero_reason = f"degraded_public_branch_blocked:{public_error or ''}"
    elif reason_type == "public_discovered":
        _checkpoint_zero_reason = f"feed_ingress_blocker:{public_discovered}"
    else:
        # Fallback for safety
        _checkpoint_zero_reason = evidence_note if evidence_note else "unknown_checkpoint_reason"

    return _ckpt_category, _checkpoint_zero_reason


def _compute_export_finish_status(
    final_phase: str,
    accepted_findings: int,
    aborted: bool,
) -> str:
    """
    Sprint F350M-R: Extracted export finish status computation.

    Reduces run_sprint cyclomatic complexity by ~5 points.
    Pure function — no side effects, no external dependencies.
    """
    if final_phase in ("EXPORT", "TEARDOWN") and accepted_findings > 0 and not aborted:
        return "finished"
    elif aborted:
        return "aborted"
    elif accepted_findings == 0:
        return "empty_run"
    else:
        return "unknown"


def _extract_result_fields(result: Any, export_finish_status: str | None) -> dict[str, Any]:
    """Extract common result fields used in canonical_run_summary construction.

    Avoids redundant result attribute access across report_dict and ExportHandoff
    canonical_run_summary dicts. All fields are direct result attributes or
    derived values that don't require additional computation.

    Args:
        result: SprintSchedulerResult with all scheduler outcome fields.
        export_finish_status: Pre-computed export finish status string.

    Returns:
        dict with all common canonical_run_summary result-based fields.
    """
    return {
        "cycles_started": result.cycles_started,
        "cycles_completed": result.cycles_completed,
        "pre_loop_elapsed_s": result.pre_loop_elapsed_s,
        "pre_loop_blocker_reason": result.pre_loop_blocker_reason,
        "pre_active_starvation": result.pre_active_starved,
        "export_finish_layer_status": export_finish_status,
        "public_error": result.public_error,
        "ct_log_discovered": result.ct_log_discovered,
        "ct_log_stored": result.ct_log_stored,
        "ct_log_accepted_findings": result.ct_log_accepted_findings,
        "cc_archive_injected": result.cc_archive_injected,
        "academic_findings_count": result.academic_findings_count,
    }


# --------------------------------------------------------------------------- #
# Concurrent pre-flight check tasks (eliminate nonlocal + nested function pattern)
# --------------------------------------------------------------------------- #


async def _malloc_task_async(result: Any) -> None:
    """Async task for malloc zone pressure relief.

    Uses result container instead of nonlocal closure capture.
    """
    from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief

    result.value = await asyncio.to_thread(malloc_zone_pressure_relief)


async def _uma_task_async(result: Any) -> None:
    """Async task for UMA status sampling.

    Uses result container instead of nonlocal closure capture.
    """
    result.value = await asyncio.to_thread(sample_uma_status)


async def _run_concurrent_checks_async() -> tuple[int | None, Any]:
    """Run malloc relief and UMA status sampling concurrently.

    Uses asyncio.TaskGroup for parallel execution. Returns tuple of
    (malloc_released_bytes, uma_status).

    Raises ExceptionGroup on TaskGroup failure to enable fallback.
    """
    class _MallocResult:
        __slots__ = ("value",)
        def __init__(self) -> None:
            self.value: int | None = None

    class _UmaResult:
        __slots__ = ("value",)
        def __init__(self) -> None:
            self.value: Any = None

    malloc_res = _MallocResult()
    uma_res = _UmaResult()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(_malloc_task_async(malloc_res))
        tg.create_task(_uma_task_async(uma_res))

    return malloc_res.value, uma_res.value


def _make_cycle_callback(dashboard: Any) -> Callable[[Any, str, float], None]:
    """
    Factory for progress callback that updates dashboard.

    Replaces closure capture of _dashboard with explicit parameter passing.
    All exceptions are swallowed fail-soft — dashboard must never block sprint.
    """
    def callback(result: Any, phase: str, elapsed_s: float) -> None:
        if dashboard is not None:
            try:
                dashboard.update(result, phase, elapsed_s)
            except Exception:  # noqa: BLE001
                pass  # fail-soft: dashboard must never block sprint
    return callback


# =============================================================================
# PHASE REFACTORING: Extracted Phase Functions (F350M-R Phase 5)
# =============================================================================


async def _run_sprint_boot(
    ctx: SprintRunContext,
    query: str,
    duration_s: float,
    windup_lead_s: float | None,
    flags: SprintFlags | None,
    force: bool,
    aggressive_mode: bool,
    resume: bool,
    prng_seed: int | None,
    replay_seed: int | None,
) -> None:
    """
    PHASE REFACTORING F350M-R: BOOT phase - pre-flight guards, initialization, lock acquisition.

    Extracted from run_sprint() to reduce cognitive complexity from ~240 to ~30.
    Handles:
    - Pre-flight guards (UMA, active window check)
    - Seed state generation (ULTIMATE-001)
    - Sprint lock acquisition (F266-LOCK)
    - DuckDB initialization with concurrent circuit breaker reset
    - ToT checkpoint recovery (UNIFIED-006)

    Args:
        ctx: SprintRunContext to populate with boot state
        query: Sprint query string
        duration_s: Requested sprint duration
        windup_lead_s: Optional windup lead time override
        flags: SprintFlags bundle
        force: Override pre-flight guards
        aggressive_mode: Aggressive mode flag
        resume: Enable checkpoint recovery
        prng_seed: Optional explicit PRNG seed
        replay_seed: Optional replay seed for deterministic replay
    """
    # Phase timing
    ctx.phase_times["BOOT"] = time.monotonic()
    ctx.cancel_event = asyncio.Event()

    # Pre-flight guards
    _effective_windup_s, _uma_pre_sprint = _run_sprint_preflight_guards(
        logger=logger,
        duration_s=duration_s,
        windup_lead_s=windup_lead_s,
        flags=flags,
        force=force,
        early_config=None,  # Will be created below with full params
    )
    ctx.effective_windup_s = _effective_windup_s
    ctx.uma_baseline_gib = _uma_pre_sprint.system_used_gib
    ctx.swap_detected_pre = _uma_pre_sprint.swap_detected
    ctx.uma_state_pre = _uma_pre_sprint.state

    # Sprint ID and query hash
    ctx.sprint_id = _make_sprint_id()
    from hledac.universal.utils.hashing import query_fingerprint
    ctx.query_hash = query_fingerprint(query) if resume else ""

    # Seed state (ULTIMATE-001)
    from hledac.universal.runtime.sprint_types import SprintSeedState
    if replay_seed is not None:
        ctx.seed_state = SprintSeedState.generate(query=query, explicit_seed=replay_seed)
        logger.info("[ULTIMATE-001] Replay mode: seed=%d, tot_iv=%s",
                    ctx.seed_state.prng_seed, ctx.seed_state.tot_iv[:8])
    elif prng_seed is not None:
        ctx.seed_state = SprintSeedState.generate(query=query, explicit_seed=prng_seed)
        logger.info("[ULTIMATE-001] Using explicit seed=%d, tot_iv=%s",
                    ctx.seed_state.prng_seed, ctx.seed_state.tot_iv[:8])
    else:
        ctx.seed_state = SprintSeedState.generate(query=query)
        logger.info("[ULTIMATE-001] Generated seed=%d, tot_iv=%s "
                    "(use --seed %d for deterministic replay)",
                    ctx.seed_state.prng_seed, ctx.seed_state.tot_iv[:8],
                    ctx.seed_state.prng_seed)
    
    global _current_sprint_seed_state
    _current_sprint_seed_state = ctx.seed_state

    ctx.phase_times["WARMUP"] = time.monotonic()

    # Power assertion (APEX-1001)
    ctx.power_assertion = PowerAssertion.acquire(reason=f"sprint_{ctx.sprint_id}")
    logger.info("[PRE-LOOP] Power assertion acquired (method=%s) — sleep prevented",
                ctx.power_assertion.method)

    # Sprint lock acquisition (F266-LOCK)
    from hledac.universal.core.graph_lock_manager import GraphLockManager
    from hledac.universal.paths import get_sprint_lock_path
    ctx.sprint_lock_path = get_sprint_lock_path(query)
    _janitor_removed = _cleanup_stale_locks(ctx.sprint_lock_path.parent, logger)
    
    try:
        ctx.sprint_lock_mgr = GraphLockManager(str(ctx.sprint_lock_path))
        if not ctx.sprint_lock_mgr.acquire(timeout_s=5.0):
            _holder = ctx.sprint_lock_mgr.holder_pid
            logger.error(f"[F266-LOCK-ABORT] Sprint with query '{query}' already running (PID={_holder})")
            sys.exit(2)
        logger.debug(f"[F266-LOCK] Acquired sprint lock: {ctx.sprint_lock_path}")
    except Exception as _lock_err:
        logger.warning(f"[F266-LOCK] Could not acquire sprint lock (continuing): {_lock_err}")

    # DuckDB initialization with concurrent operations
    from hledac.universal.knowledge.duckdb_store import make_shadow_store
    ctx.store = make_shadow_store()

    # Concurrent boot operations
    from hledac.universal.runtime.prewarm_daemon import start_prewarm_if_needed
    start_prewarm_if_needed()
    
    from hledac.universal.utils.patterns.pattern_matcher import prewarm as prewarm_patterns
    prewarm_patterns()

    # Parallel DuckDB init + circuit breaker reset
    _cb_reset_coro = _reset_circuit_breakers_async(logger)
    
    with contextlib.suppress(asyncio.CancelledError):
        _init_results = await parallel_ok(
            _duckdb_init_coro(ctx.store, logger),
            _cb_reset_coro,
            label="pre_init",
        )
    
    if _init_results:
        _duckdb_result = _init_results[0]
        ctx.duckdb_init_ok = not isinstance(_duckdb_result, Exception) and _duckdb_result
        if not ctx.duckdb_init_ok:
            logger.warning(f"[P0-3] DuckDB pre-init failed (fail-soft): {_duckdb_result}")

    # ToT checkpoint recovery (UNIFIED-006)
    if ctx.duckdb_init_ok and resume:
        ctx.resume_from, ctx.resume_step = await _attempt_tot_recovery(
            ctx.store, ctx.query_hash, logger
        )


async def _attempt_tot_recovery(
    store: Any,
    query_hash: str,
    logger: logging.Logger,
) -> tuple[dict | None, int]:
    """
    Attempt to recover ToT checkpoint from a crashed sprint.
    
    Returns:
        Tuple of (nodes_dict or None, resume_step)
    """
    try:
        from hledac.universal.coordinators.meta_reasoning_coordinator import ThoughtNode
        
        _orphan_row = await store.async_get_latest_tot_checkpoint_by_query_hash(query_hash=query_hash)
        if _orphan_row is None:
            return None, 0
            
        _orphan_step, _tree_json_str, _ts, _stored_checksum, _orphan_sprint_id = _orphan_row
        
        # Verify checksum
        _raw = _tree_json_str.encode("utf-8")
        _computed = hashlib.blake2b(_raw, digest_size=32).hexdigest()
        if _computed != _stored_checksum:
            logger.error("[UNIFIED-006] Checksum mismatch — checkpoint corrupt, starting fresh")
            return None, 0
        
        _envelope = orjson.loads(_raw)
        _raw_nodes = _envelope.get("nodes", {})
        
        _nodes: dict[str, ThoughtNode] = {}
        for _nid, _ndata in _raw_nodes.items():
            try:
                _nodes[_nid] = ThoughtNode(
                    node_id=_ndata.get("node_id", _nid),
                    thought=_ndata.get("thought", ""),
                    value_estimate=_ndata.get("value_estimate", 0.0),
                    parent=_ndata.get("parent"),
                    children=_ndata.get("children", []),
                    visited=_ndata.get("visited", False),
                    expanded=_ndata.get("expanded", False),
                    depth=_ndata.get("depth", 0),
                    cost=_ndata.get("cost", 0.0),
                    uncertainty=_ndata.get("uncertainty", 0.0),
                )
            except Exception:
                pass
        
        if _nodes:
            logger.warning(
                "[UNIFIED-006] 🔄 RESUMING ToT from checkpoint: orphan_sprint=%s step=%d nodes=%d",
                _orphan_sprint_id[:12], _orphan_step, len(_nodes)
            )
            return _nodes, _orphan_step
        else:
            logger.warning("[UNIFIED-006] Checkpoint found but all nodes failed deserialization")
            return None, 0
            
    except Exception:
        pass  # fail-soft: checkpoint probe must never block sprint start
    return None, 0


async def _run_sprint_execute(
    ctx: SprintRunContext,
    query: str,
    duration_s: float,
    aggressive_mode: bool,
    deep_research: bool,
    extreme_mode: bool,
    acquisition_profile: str | None,
    flags: SprintFlags | None,
    rl_train_mode: bool,
    ui_mode: bool,
    export_dir: str,
) -> None:
    """
    PHASE REFACTORING F350M-R: EXECUTE phase - scheduler setup, sprint race, execution.
    
    Delegates to extracted helper functions for scheduler setup, pre-flight checks,
    dashboard, and the scheduler race.
    """
    # Phase 1: Scheduler setup
    await _execute_scheduler_setup(
        ctx=ctx, query=query, duration_s=duration_s,
        aggressive_mode=aggressive_mode, deep_research=deep_research,
        extreme_mode=extreme_mode, acquisition_profile=acquisition_profile,
        flags=flags, rl_train_mode=rl_train_mode, export_dir=export_dir,
    )
    
    # Phase 2: Pre-flight checks
    health = await _execute_preflight_checks(ctx, flags)
    
    # Phase 3: Dashboard
    ctx.live_feed_urls = _get_live_feed_urls()
    await _execute_dashboard(ctx, ui_mode, query, duration_s)
    
    # Phase 4: Execute sprint race
    await _execute_sprint_race(ctx, query)
    
    # Phase 5: Cleanup
    await _execute_cleanup(ctx)


async def _execute_scheduler_setup(
    ctx: SprintRunContext, query: str, duration_s: float,
    aggressive_mode: bool, deep_research: bool, extreme_mode: bool,
    acquisition_profile: str | None, flags: SprintFlags | None,
    rl_train_mode: bool, export_dir: str,
) -> None:
    """Phase 1: Scheduler configuration, creation, BlitzGC, V2Init."""
    from hledac.universal.runtime.scheduler_config import SprintSchedulerConfig
    from hledac.universal.runtime.scheduler_v2 import SprintSchedulerV2
    from hledac.universal.runtime.scheduler_v2._v2_init import V2Init

    # Configure acquisition profile
    _acq_input = acquisition_profile
    _acq_effective = acquisition_profile
    if _acq_effective == "nonfeed_diagnostic180":
        _acq_effective = "nonfeed_diagnostic"
    if _acq_effective not in ("default", "nonfeed_diagnostic", "deep_osint_m1"):
        logger.warning("[F228A] Unknown acquisition_profile=%r normalized to 'default'", _acq_input)
        _acq_effective = "default"
    if "HLEDAC_ACQUISITION_PROFILE" not in os.environ:
        os.environ["HLEDAC_ACQUISITION_PROFILE"] = _acq_effective or "default"
    acquisition_profile = _acq_effective or "default"

    # Create scheduler config
    config = SprintSchedulerConfig(
        sprint_duration_s=float(duration_s), windup_lead_s=ctx.effective_windup_s,
        export_enabled=True, export_dir=export_dir, aggressive_mode=aggressive_mode,
        branch_timeout_budget_s=8.0 if aggressive_mode else 0.0,
        acquisition_profile=acquisition_profile, deep_research_enabled=deep_research,
        extreme_mode=extreme_mode,
    )

    # Blitz mode (BLITZ-12)
    _blitz = getattr(flags, 'blitz_mode', False) if flags else False
    if _blitz:
        from hledac.universal.core.telemetry.context_state import set_blitz_mode as _set_blitz
        _set_blitz(True)
        logger.info("[BLITZ-12] Blitz mode enabled")
        from hledac.universal.fetching.public_fetcher import reset_blitz_dead_hosts
        reset_blitz_dead_hosts()

    # Create scheduler
    ctx.scheduler = SprintSchedulerV2(_config=config, _flags=flags)

    # Activate BlitzGC (PHYSICS-06/07)
    with _fail_safe_async("debug", "blitz_gc.sprint_start"):
        from hledac.universal.coordinators.resource.blitz_gc import blitz_gc
        _blitz_telemetry = blitz_gc.sprint_start()
        logger.info("[PHYSICS-06] BlitzGC active — GC disabled for active sprint window")

    # V2Init - unified initialization
    _wall_clock_start = ctx.phase_times.get("WARMUP", ctx.phase_times["BOOT"])
    _init = V2Init(ctx.scheduler)
    await _init.run(
        query, _wall_clock_start, ctx=None, cancel_event=ctx.cancel_event,
        flags=flags, sprint_id=ctx.sprint_id, sprint_duration_s=float(duration_s),
        windup_lead_s=ctx.effective_windup_s, duckdb_store=ctx.store,
        rl_train_mode=rl_train_mode, logger=logger,
        resume_from=ctx.resume_from, resume_step=ctx.resume_step, query_hash=ctx.query_hash,
    )

    # Get EvidenceLog reference
    ctx.evidence_log = ctx.scheduler._evidence_log.value if ctx.scheduler._evidence_log else None


async def _execute_preflight_checks(ctx: SprintRunContext, flags: SprintFlags | None):
    """Phase 2: Health check and production guard."""
    # Health check (F228F)
    health = None
    try:
        async with asyncio.timeout(30.0):
            health = await ctx.scheduler.health_check()
    except TimeoutError:
        logger.warning("[F228F] health_check timed out after 30s")

    if health is not None and not health.overall_ok:
        logger.warning(f"[F228F] health_check warnings: {health.summary()}")
    elif health is not None:
        logger.debug(f"[F228F] health_check: {health.summary()}")

    # Production pre-flight guard (F272B)
    if (flags.production if flags else False) and health is not None and not health.fetch_coordinator_ok:
        logger.error("[F272B] --production pre-flight ABORT: fetch coordinator not_initialized")
        sys.exit(2)

    # CT log client (F193A)
    ctx.ct_log_client = None
    with _fail_safe_async("debug", "ct_log_client.init"):
        from hledac.universal.intel.ct_log_client import CTLogClient
        _ct_cache = Path.home() / ".hledac" / "ct_cache"
        _ct_cache.mkdir(parents=True, exist_ok=True)
        ctx.ct_log_client = CTLogClient(cache_dir=_ct_cache)

    return health


async def _execute_dashboard(ctx: SprintRunContext, ui_mode: bool, query: str, duration_s: float) -> None:
    """Phase 3: Dashboard setup."""
    if ui_mode:
        with _fail_safe_async("warning", "dashboard.create"):
            from hledac.universal.monitoring.sprint_dashboard import SprintDashboard
            ctx.dashboard = SprintDashboard(ctx.sprint_id, query, duration_s)
            ctx.dashboard.start()


async def _execute_sprint_race(ctx: SprintRunContext, query: str) -> None:
    """Phase 4: Scheduler vs cancel race with cooperative shutdown."""
    from hledac.universal.utils.mlx_cache import start_memory_status_poller

    _make_cycle_callback(ctx.dashboard)  # Register callback
    
    # Start memory status poller (ISSUE-7.2)
    with _fail_safe_async("debug", "memory_status_poller.start"):
        await start_memory_status_poller(interval_s=0.5)

    # Race: scheduler.run() vs cancel_event
    _cancel_waiter = safe_create_task(ctx.cancel_event.wait(), eager_start=True)
    _scheduler_waiter = safe_create_task(ctx.scheduler.run(query), eager_start=True)

    try:
        _first_result, winner_task = await first_completed(_scheduler_waiter, _cancel_waiter)
    except asyncio.TimeoutError:
        raise

    if winner_task is _scheduler_waiter and winner_task.done():
        ctx.result = _first_result
        _cancel_waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _cancel_waiter
    else:
        # Cooperative shutdown
        _scheduler_waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _scheduler_waiter
        from hledac.universal.runtime.scheduler_result import SprintSchedulerResult
        _sf = SprintSchedulerResult()
        _sf.scheduler_exit_path = "cooperative_shutdown"
        _sf.scheduler_exit_reason = "SIGINT/SIGTERM received via shutdown_event"
        ctx.result = _sf


async def _execute_cleanup(ctx: SprintRunContext) -> None:
    """Phase 5: Memory poller stop and lock release."""
    # Stop memory status poller
    with _fail_safe_async("debug", "memory_status_poller.stop"):
        from hledac.universal.utils.mlx_cache import stop_memory_status_poller
        await stop_memory_status_poller()

    # Release sprint lock
    if ctx.sprint_lock_mgr is not None:
        with _fail_safe_async("debug", "sprint_lock.release"):
            ctx.sprint_lock_mgr.release()
            logger.debug("[F266-LOCK] Released sprint lock")


async def _run_sprint_windup(
    ctx: SprintRunContext,
    query: str,
    duration_s: float,
    export_dir: str,
    deep_probe_enabled: bool,
) -> None:
    """
    PHASE REFACTORING F350M-R: WINDUP phase - result processing, report generation, export.
    
    Delegates to extracted helper functions for each phase of windup processing.
    """
    ctx.phase_times["WINDUP"] = time.monotonic()
    result = ctx.result
    actual_duration = ctx.phase_times.get("TEARDOWN", ctx.phase_times["WINDUP"]) - ctx.phase_times["BOOT"]
    time_to_windup_s = ctx.phase_times["WINDUP"] - ctx.phase_times["BOOT"]

    # Phase 1: Early windup - events, CT discovery, sprint delta
    await _windup_phase1_early(ctx, query, actual_duration)
    
    # Phase 2: Timing - phase durations and scheduler intelligence
    _phase_durations = _windup_phase2_timing(ctx, duration_s)
    
    # Phase 3: Compute metrics and classifications
    metrics = _windup_phase3_compute_metrics(ctx, result, actual_duration)
    classifications = _windup_phase3_compute_classifications(ctx, result, metrics, query, duration_s)
    
    # Phase 4: Logging
    _windup_phase4_logging(ctx, result, classifications, metrics)
    
    # Phase 5: Build and write reports
    ctx.report_path = get_sprint_json_report_path(ctx.sprint_id)
    _acq_payload = _scheduler_result_acquisition_payload(result, ctx.scheduler, query, duration_s)
    _acq_payload_filtered = {k: v for k, v in _acq_payload.items() if k != "source_family_outcomes"}
    
    report_dict = _build_report_dict(
        inp=ReportBuildInput(
            query=query,
            duration_s=duration_s,
            actual_duration=actual_duration,
            feed_fnd=metrics["feed_fnd"],
            dup_rate=metrics["dup_rate"],
            findings_per_min=metrics["findings_per_min"],
            public_pct=metrics["public_pct"],
            src_mix_str=metrics["src_mix_str"],
            verdict=classifications["verdict"],
            next_hint=classifications["next_hint"],
            phase_durations=_phase_durations,
            runtime_truth=classifications["runtime_truth"],
            timing_truth=classifications["timing_truth"],
            runtime_truth_level=classifications["runtime_truth_level"],
            observed_run_tuple=classifications["observed_run_tuple"],
            ckpt_category=classifications["ckpt_category"],
            checkpoint_zero_reason=classifications["checkpoint_zero_reason"],
            export_finish_status=classifications["export_finish_status"],
            uma_peak_gib=classifications["uma_peak_gib"],
            ctx=ctx,
            result=result,
            acq_payload_filtered=_acq_payload_filtered,
        )
    )
    ctx.report_path.write_bytes(_serialize_report(report_dict))
    logger.info(f"[REPORT] {ctx.report_path}")

    # Phase 6: Export handoff
    await _windup_phase5_export(
        ctx, result, _phase_durations, _acq_payload,
        metrics["src_mix_str"], classifications, deep_probe_enabled,
        query, actual_duration,
    )


async def _windup_phase1_early(ctx: SprintRunContext, query: str, actual_duration: float) -> None:
    """Phase 1: Early windup - events, CT discovery, sprint delta."""
    result = ctx.result
    
    # Record WINDUP phase event
    if ctx.evidence_log is not None:
        with _fail_safe_async("debug", "evidence_log.windup_event"):
            ctx.evidence_log.create_event(
                event_type="observation",
                payload={"phase": "WINDUP", "sprint_id": ctx.sprint_id, "query": query},
                confidence=1.0,
            )

    # CT log discovery (if not aggressive mode)
    if not ctx.scheduler._config.aggressive_mode:
        with _fail_safe_async("debug", "ct_log_discovery"):
            await ctx.scheduler._run_ct_log_discovery_in_cycle(query=query, store=ctx.store)
            result.accepted_findings += result.ct_log_stored

    # Write sprint delta
    uma_peak_gib = sample_uma_status().system_used_gib
    await write_sprint_delta(
        store=ctx.store, sprint_id=ctx.sprint_id, query=query,
        new_findings=result.accepted_findings,
        dedup_hits=result.duplicate_entry_hashes_skipped,
        ioc_nodes=result.unique_entry_hashes_seen,
        uma_baseline_gib=ctx.uma_baseline_gib, uma_peak_gib=uma_peak_gib,
        synthesis_success=result.accepted_findings > 0, duration_s=actual_duration,
        hits_per_source=result.hits_per_source, seed_state=ctx.seed_state,
    )

    # EvidenceLog teardown
    if ctx.evidence_log is not None:
        with _fail_safe_async("warning", "evidence_log.teardown"):
            ctx.evidence_log.finalize()
            ctx.evidence_log.freeze()
    
    ctx.phase_times["TEARDOWN"] = time.monotonic()
    
    # Record TEARDOWN phase event
    if ctx.evidence_log is not None:
        with _fail_safe_async("debug", "evidence_log.teardown_event"):
            ctx.evidence_log.create_event(
                event_type="observation",
                payload={"phase": "TEARDOWN", "sprint_id": ctx.sprint_id, "actual_duration_s": round(actual_duration, 2)},
                confidence=1.0,
            )


def _windup_phase2_timing(ctx: SprintRunContext, duration_s: float) -> dict[str, float]:
    """Phase 2: Compute phase timings and get scheduler intelligence."""
    phases = _PHASE_ORDER
    _phase_durations: dict[str, float] = {}
    for i, ph in enumerate(phases):
        ph_name = ph.name
        if ph_name in ctx.phase_times:
            next_ph = phases[i + 1] if i + 1 < len(phases) else None
            if next_ph is not None and next_ph.name in ctx.phase_times:
                _phase_durations[ph_name] = round(ctx.phase_times[next_ph.name] - ctx.phase_times[ph_name], 2)
    
    with _fail_safe_async("debug", "compute_sprint_intelligence"):
        ctx.intel = ctx.scheduler.compute_sprint_intelligence() or {}
    return _phase_durations


def _windup_phase3_compute_metrics(ctx: SprintRunContext, result: Any, actual_duration: float) -> dict:
    """Phase 3a: Compute derived metrics."""
    findings_per_min = (result.accepted_findings / (actual_duration / 60.0)) if actual_duration > 0 else 0.0
    total_seen = result.unique_entry_hashes_seen + result.duplicate_entry_hashes_skipped
    dup_rate = (result.duplicate_entry_hashes_skipped / total_seen * 100) if total_seen > 0 else 0.0
    feed_fnd = result.accepted_findings - result.public_accepted_findings
    public_pct = (result.public_accepted_findings / result.accepted_findings * 100) if result.accepted_findings > 0 else 0.0
    
    src_mix: list[str] = []
    for src, cnt in sorted(result.hits_per_source.items(), key=lambda x: x[1], reverse=True):
        src_mix.append(f"{src}={cnt}")
    src_mix_str = ", ".join(src_mix) if src_mix else "none"
    
    return {
        "findings_per_min": findings_per_min,
        "dup_rate": dup_rate,
        "feed_fnd": feed_fnd,
        "public_pct": public_pct,
        "src_mix_str": src_mix_str,
    }


def _windup_phase3_compute_classifications(ctx: SprintRunContext, result: Any, metrics: dict, query: str, duration_s: float) -> dict:
    """Phase 3b: Compute verdict, runtime_truth, timing_truth, runtime_truth_level, checkpoint category."""
    time_to_windup_s = ctx.phase_times["WINDUP"] - ctx.phase_times["BOOT"]
    actual_duration = ctx.phase_times.get("TEARDOWN", ctx.phase_times["WINDUP"]) - ctx.phase_times["BOOT"]
    
    # Timing truth
    pre_scheduler_boot_s = ctx.phase_times.get("WARMUP", 0) - ctx.phase_times["BOOT"]
    _windup_mark = ctx.phase_times.get("WINDUP", ctx.phase_times.get("TEARDOWN", ctx.phase_times["BOOT"]))
    scheduler_wall_s = _windup_mark - ctx.phase_times.get("WARMUP", ctx.phase_times["BOOT"])
    windup_lead_observed_s = ctx.phase_times["TEARDOWN"] - ctx.phase_times.get("WINDUP", 0)
    
    timing_truth = {
        "requested_duration_s": duration_s,
        "windup_lead_s": ctx.scheduler._config.windup_lead_s,
        "time_to_windup_s": round(time_to_windup_s, 2),
        "time_to_teardown_s": round(ctx.phase_times["TEARDOWN"] - ctx.phase_times["BOOT"], 2),
        "active_window_budget_s": round(duration_s - ctx.effective_windup_s, 2),
        "windup_lead_observed_s": round(windup_lead_observed_s, 2),
        "pre_scheduler_boot_s": round(pre_scheduler_boot_s, 2),
        "scheduler_wall_s": round(scheduler_wall_s, 2),
        "scheduler_returned_phase": "ACTIVE" if result.entered_active_at_monotonic else "entry_only",
        "entered_active_truth": result.entered_active_at_monotonic is not None,
        "first_cycle_truth": result.first_cycle_started_at_monotonic is not None,
        "pre_active_starvation": result.pre_active_starved,
        "pre_active_blocker": result.pre_loop_blocker_reason or None,
    }

    # Hardware-limited checks
    _inline_hardware_limited = (
        result.accepted_findings == 0 and result.total_pattern_hits == 0
        and result.cycles_started == 0
        and (ctx.swap_detected_pre or ctx.uma_state_pre in ("critical", "emergency"))
    )
    _is_hardware_limited = (
        not metrics.get("is_meaningful", False) and result.cycles_started == 0
        and (ctx.swap_detected_pre or ctx.uma_state_pre in ("critical", "emergency"))
    )
    _is_pre_active_mem_starved = (
        not metrics.get("is_meaningful", False) and result.cycles_started == 0
        and result.entered_active_at_monotonic is not None
        and (ctx.swap_detected_pre or ctx.uma_state_pre in ("critical", "emergency", "warn"))
    )

    # Verdict
    verdict, next_hint = _compute_verdict_and_hint(
        inp=VerdictHintInput(
            aborted=result.aborted,
            accepted_findings=result.accepted_findings,
            dup_rate=metrics["dup_rate"],
            public_pct=metrics["public_pct"],
            feed_fnd=metrics["feed_fnd"],
            hardware_limited=_inline_hardware_limited,
            public_backend_degraded=result.public_backend_degraded,
            public_discovered=result.public_discovered,
            total_pattern_hits=result.total_pattern_hits,
            public_fetched=result.public_fetched,
            stop_requested=result.stop_requested,
        )
    )

    # Runtime truth
    _total_ct = (result.lane_ct_accepted_findings or 0) + (result.ct_log_stored or 0)
    runtime_truth = _runtime_truth(
        inp=RuntimeTruthInput(
            actual_duration_s=actual_duration,
            query=query,
            duration_s=duration_s,
            cycles_completed=result.cycles_completed,
            cycles_started=result.cycles_started,
            accepted_findings=result.accepted_findings,
            total_pattern_hits=result.total_pattern_hits,
            public_accepted_findings=result.public_accepted_findings,
            feed_findings=metrics["feed_fnd"],
            ct_findings=_total_ct,
            swap_detected=ctx.swap_detected_pre,
            uma_state=ctx.uma_state_pre,
            branch_timeout_count=result.branch_timeout_count,
            public_branch_timed_out=result.public_branch_timed_out,
            ct_branch_timed_out=result.ct_branch_timed_out,
        )
    )
    is_meaningful = runtime_truth["is_meaningful"]
    evidence_note = runtime_truth["evidence_note"]
    timing_truth["active_runtime_occurred"] = is_meaningful and time_to_windup_s > 0

    # Runtime truth level
    runtime_truth_level = (
        "active" if is_meaningful and result.accepted_findings > 0
        else "pre_active_memory_starvation" if _is_pre_active_mem_starved
        else "survival_active_minimal" if is_meaningful and ctx.uma_state_pre in ("warn", "critical", "emergency")
        else "hardware_limited_smoke" if _is_hardware_limited
        else "short_signal" if is_meaningful and result.total_pattern_hits > 0
        else "meaningful_empty" if is_meaningful
        else "smoke"
    )

    # Observed run tuple
    observed_run_tuple = (
        query[:40] if len(query) > 40 else query,
        round(actual_duration, 1), result.cycles_completed,
        metrics["src_mix_str"], runtime_truth_level,
    )

    # Checkpoint category
    _public_backend = result.public_backend_degraded
    _feed_zero_check = result.accepted_findings == 0 and metrics["feed_fnd"] == 0
    _cross_branch_fail_check = (
        result.accepted_findings == 0 and result.total_pattern_hits > 0
        and not _public_backend and not result.public_error
    )
    _ckpt_category, _checkpoint_zero_reason = _compute_checkpoint_category(
        inp=CheckpointInput(
            accepted_findings=result.accepted_findings,
            total_pattern_hits=result.total_pattern_hits,
            public_error=result.public_error,
            public_discovered=result.public_discovered,
            public_backend=_public_backend,
            feed_zero_check=_feed_zero_check,
            cross_branch_fail_check=_cross_branch_fail_check,
            is_pre_active_mem_starved=_is_pre_active_mem_starved,
            is_hardware_limited=_is_hardware_limited,
            is_meaningful=is_meaningful,
            uma_state_pre=ctx.uma_state_pre,
            feed_fnd=metrics["feed_fnd"],
            phase_times=ctx.phase_times,
        ),
        evidence_note=evidence_note,
    )
    _export_finish_status = _compute_export_finish_status(
        final_phase=result.final_phase, accepted_findings=result.accepted_findings, aborted=result.aborted,
    )

    uma_peak_gib = sample_uma_status().system_used_gib
    
    return {
        "verdict": verdict, "next_hint": next_hint,
        "runtime_truth": runtime_truth, "timing_truth": timing_truth,
        "runtime_truth_level": runtime_truth_level, "observed_run_tuple": observed_run_tuple,
        "ckpt_category": _ckpt_category, "checkpoint_zero_reason": _checkpoint_zero_reason,
        "export_finish_status": _export_finish_status, "uma_peak_gib": uma_peak_gib,
        "is_meaningful": is_meaningful, "evidence_note": evidence_note,
    }


def _windup_phase4_logging(ctx: SprintRunContext, result: Any, classifications: dict, metrics: dict) -> None:
    """Phase 4: Log runtime truth, sprint done, summary, next hint, sources, intel."""
    is_meaningful = classifications["is_meaningful"]
    evidence_note = classifications["evidence_note"]

    if is_meaningful:
        logger.info(f"[RUNTIME TRUTH] ✅ MEANINGFUL ACTIVE RUN | {evidence_note}")
    else:
        logger.warning(f"[RUNTIME TRUTH] 🚨 SMOKE ONLY | {evidence_note}")

    logger.info(f"[SPRINT DONE] {ctx.sprint_id} | findings: {result.accepted_findings}")
    logger.info(f"[SUMMARY] {classifications['verdict']}")
    logger.info(f"[NEXT] {classifications['next_hint']}")
    logger.info(f"[SOURCES] {metrics['src_mix_str']}")

    sv = ctx.intel.get("sprint_verdict") or {}
    if sv:
        logger.info(f"[INTEL] posture={sv.get('posture', '?')} | dominant={sv.get('dominant_signal', '?')}")


async def _windup_phase5_export(
    ctx: SprintRunContext, result: Any, phase_durations: dict, acq_payload: dict,
    src_mix_str: str, classifications: dict, deep_probe_enabled: bool,
    query: str, actual_duration: float,
) -> None:
    """Phase 5: Export handoff and deep probe."""
    runtime_truth = classifications["runtime_truth"]
    timing_truth = classifications["timing_truth"]
    runtime_truth_level = classifications["runtime_truth_level"]
    observed_run_tuple = classifications["observed_run_tuple"]
    ckpt_category = classifications["ckpt_category"]
    checkpoint_zero_reason = classifications["checkpoint_zero_reason"]
    export_finish_status = classifications["export_finish_status"]

    with _fail_safe_async("warning", "sprint_exporter"):
        from hledac.universal.export.sprint_exporter import export_sprint

        top_seed_nodes = []
        with _fail_safe_async("debug", "get_top_seed_nodes"):
            top_seed_nodes = ctx.store.get_top_seed_nodes(n=5) if ctx.store else []

        handoff = _build_export_handoff(
            inp=ExportHandoffInput(
                query=query,
                duration_s=ctx.duration_s,
                actual_duration=actual_duration,
                runtime_truth=runtime_truth,
                timing_truth=timing_truth,
                runtime_truth_level=runtime_truth_level,
                observed_run_tuple=observed_run_tuple,
                src_mix_str=src_mix_str,
                ckpt_category=ckpt_category,
                checkpoint_zero_reason=checkpoint_zero_reason,
                export_finish_status=export_finish_status,
                phase_durations=phase_durations,
                ctx=ctx,
                result=result,
                top_seed_nodes=top_seed_nodes,
                live_feed_urls=ctx.live_feed_urls,
                acq_payload=acq_payload,
            )
        )

        _elog_instance = ctx.scheduler._evidence_log.value if ctx.scheduler._evidence_log else None
        export_result = await export_sprint(
            store=ctx.store, handoff=handoff, sprint_id=ctx.sprint_id, evidence_log=_elog_instance,
        )
        logger.info(f"[EXPORT] finish layer → seeds={export_result.get('seeds_json', '')}")

        if deep_probe_enabled:
            with _fail_safe_async("warning", "deep_probe"):
                from hledac.universal.deep_research.probe_runner import run_deep_probe_if_enabled
                probe_result = await run_deep_probe_if_enabled(
                    query=query, store=ctx.store, deep_probe_enabled=True
                )
                if probe_result:
                    logger.info(f"[DEEP_PROBE] completed: {probe_result}")


def _build_report_dict(inp: ReportBuildInput) -> dict:
    """
    Sprint F350M-R: Build the canonical report dictionary using bundled input.
    
    Reduces function signature from 23 parameters to 1.
    """
    # Unpack input for convenience in internal usage
    ctx = inp.ctx
    result = inp.result
    
    result_dict = {
        "sprint_id": ctx.sprint_id,
        "query": inp.query,
        "duration_s": inp.duration_s,
        "actual_duration_s": inp.actual_duration,
        "accepted_findings": result.accepted_findings,
        "feed_findings": inp.feed_fnd,
        "public_accepted_findings": result.public_accepted_findings,
        "public_discovered": result.public_discovered,
        "public_fetched": result.public_fetched,
        "public_matched_patterns": result.public_matched_patterns,
        "public_stored_findings": result.public_stored_findings,
        "public_error": result.public_error,
        "ct_log_discovered": result.ct_log_discovered,
        "ct_log_stored": result.ct_log_stored,
        "ct_log_accepted_findings": result.ct_log_accepted_findings,
        "ct_log_error": result.ct_log_error,
        "cycles_completed": result.cycles_completed,
        "cycles_started": result.cycles_started,
        "unique_entry_hashes_seen": result.unique_entry_hashes_seen,
        "duplicate_entry_hashes_skipped": result.duplicate_entry_hashes_skipped,
        "total_pattern_hits": result.total_pattern_hits,
        "dup_rate_pct": round(inp.dup_rate, 2),
        "findings_per_min": round(inp.findings_per_min, 2),
        "final_phase": result.final_phase,
        "aborted": result.aborted,
        "abort_reason": result.abort_reason,
        "stop_requested": result.stop_requested,
        "entries_per_source": result.entries_per_source,
        "hits_per_source": result.hits_per_source,
        "export_paths": result.export_paths,
        "uma_peak_gib": inp.uma_peak_gib - ctx.uma_baseline_gib,
        "synthesis_success": result.accepted_findings > 0,
        "verdict": inp.verdict,
        "next_hint": inp.next_hint,
        "phase_timing": inp.phase_durations,
        "runtime_truth": inp.runtime_truth,
        "correlation_summary": ctx.intel.get("correlation"),
        "hypothesis_pack_summary": ctx.intel.get("hypothesis_pack"),
        "signal_path": ctx.intel.get("signal_path"),
        "feed_verdict": ctx.intel.get("feed_verdict"),
        "public_verdict": ctx.intel.get("public_verdict"),
        "branch_value": ctx.intel.get("branch_value"),
        "sprint_verdict": ctx.intel.get("sprint_verdict"),
        "execution_context": {
            "query": inp.query,
            "requested_duration_s": inp.duration_s,
            "actual_duration_s": round(inp.actual_duration, 2),
            "source_count": len(ctx.live_feed_urls),
            "sources": ctx.live_feed_urls,
            "platform": _PLATFORM_INFO.copy(),
            "report_path": str(ctx.report_path),
            "git_snapshot": "unknown",
            "export_dir": str(ctx.report_path.parent),
        },
    }

    # Canonical run summary
    intel = ctx.intel
    crs = {
        "meaningful": inp.runtime_truth["is_meaningful"],
        "primary_signal": inp.runtime_truth["primary_signal_source"],
        "posture": (intel.get("sprint_verdict") or {}).get("posture", "unknown"),
        "dominant_signal_path": (intel.get("signal_path") or {}).get("dominant_signal_path", "unknown"),
        "corroborated": (intel.get("signal_path") or {}).get("is_corroborated", False),
        "is_noisy": (intel.get("signal_path") or {}).get("is_noisy", False),
        "next_pivot": (intel.get("signal_path") or {}).get("next_pivot_recommendation", "unknown"),
        "branch_verdict": (intel.get("branch_value") or {}).get("branch_verdict", "unknown"),
        "risk_score": (intel.get("correlation") or {}).get("risk_score", 0.0),
        "hypothesis_count": (intel.get("hypothesis_pack") or {}).get("hypothesis_count", 0),
        "first_action": (intel.get("sprint_verdict") or {}).get("first_action", ""),
        "confidence": (intel.get("sprint_verdict") or {}).get("confidence", ""),
        "runtime_truth_level": inp.runtime_truth_level,
        "checkpoint_zero_category": inp.ckpt_category,
        "checkpoint_zero_reason": inp.checkpoint_zero_reason,
        "observed_run_tuple": inp.observed_run_tuple,
        "canonical_sprint_owner": "runtime.sprint_entrypoint.run_sprint",
        "canonical_path_used": "run_sprint",
        "effective_source_mix": inp.src_mix_str,
        "effective_parallelism": len(ctx.live_feed_urls),
        "effective_timeouts": {},
        "active_iteration_count": result.cycles_completed,
        **_extract_result_fields(result, inp.export_finish_status),
        "timing_truth": inp.timing_truth,
        "early_exit_class": getattr(result, "early_exit_class", ""),
        "early_exit_reason": getattr(result, "early_exit_reason", ""),
        "requested_duration_s": inp.duration_s,
        "actual_duration_s": round(inp.actual_duration, 2),
        "elapsed_pct": round((inp.actual_duration / inp.duration_s) * 100, 1) if inp.duration_s > 0 else 0.0,
        "active_window_budget_s": inp.timing_truth["active_window_budget_s"],
        "active_window_elapsed_s": inp.timing_truth["time_to_windup_s"],
        "governor_uma_state": getattr(result, "governor_uma_state", ""),
        "governor_system_used_gib": getattr(result, "governor_system_used_gib", 0.0),
        "governor_swap_detected": getattr(result, "governor_swap_detected", False),
        "governor_io_only": getattr(result, "governor_io_only", False),
    }
    result_dict["canonical_run_summary"] = crs

    # Merge acquisition payload
    result_dict.update(inp.acq_payload_filtered)
    result_dict["timing_truth"] = inp.timing_truth
    result_dict["gc_telemetry"] = {}
    result_dict["nonfeed_mission_active"] = getattr(result, "nonfeed_mission_active", False)
    result_dict["nonfeed_required_families"] = getattr(result, "nonfeed_required_families", ())
    result_dict["nonfeed_optional_families"] = getattr(result, "nonfeed_optional_families", ())
    result_dict["nonfeed_family_status"] = getattr(result, "nonfeed_family_status", {})
    result_dict["nonfeed_all_required_terminal"] = getattr(result, "nonfeed_all_required_terminal", False)
    result_dict["nonfeed_any_accepted"] = getattr(result, "nonfeed_any_accepted", False)
    result_dict["nonfeed_provider_failures"] = getattr(result, "nonfeed_provider_failures", ())
    result_dict["nonfeed_memory_skips"] = getattr(result, "nonfeed_memory_skips", ())
    result_dict["nonfeed_mission_exit_reason"] = getattr(result, "nonfeed_mission_exit_reason", "")

    # DuckDB stats
    if ctx.store:
        result_dict["duckdb_stats"] = getattr(ctx.store, "get_stats", lambda: {})()

    # Rust extensions
    try:
        _ext_mod = __import__("hledac.universal.core.rust_backend", fromlist=["rust"]).rust.raw.module
        if _ext_mod is not None:
            _stat_collector = __import__("core.rust_backend.stats", fromlist=[""]).StatCollector
            result_dict["rust_extensions"] = _stat_collector().collect(_ext_mod)
        else:
            result_dict["rust_extensions"] = {}
    except Exception:
        result_dict["rust_extensions"] = {}

    # Evidence manifest
    if ctx.evidence_log is not None:
        result_dict["evidence_manifest"] = {
            "total_count": ctx.evidence_log.size,
            "ram_size": ctx.evidence_log.ram_size,
            "persist_path": str(ctx.evidence_log.persist_path) if ctx.evidence_log.persist_path else None,
        }
    else:
        result_dict["evidence_manifest"] = {"total_count": 0, "ram_size": 0, "persist_path": None, "note": "elog_not_wired"}

    return result_dict


def _build_export_handoff(inp: ExportHandoffInput) -> Any:
    """
    Sprint F350M-R: Build the ExportHandoff object using bundled input.
    
    Reduces function signature from 17 parameters to 1.
    """
    from hledac.universal.project_types import ExportHandoff
    
    # Unpack input for convenience
    ctx = inp.ctx
    result = inp.result
    
    intel = ctx.intel
    scorecard = {
        "synthesis_engine_used": "hermes3",
        "gnn_predicted_links": 0,
        "top_graph_nodes": inp.top_seed_nodes,
        "phase_duration_seconds": inp.phase_durations,
        "runtime_accepted_findings": (result.accepted_findings or 0) + (result.public_accepted_findings or 0),
        "findings_per_minute": round(
            ((result.accepted_findings or 0) + (result.public_accepted_findings or 0)) / (inp.actual_duration / 60.0),
            2,
        ) if inp.actual_duration > 0 else 0.0,
        "identity_candidates_found": result.identity_candidates_found,
        "identity_findings_produced": result.identity_findings_produced,
        **inp.acq_payload,
    }

    canonical_run_summary = {
        "meaningful": inp.runtime_truth["is_meaningful"],
        "primary_signal": inp.runtime_truth["primary_signal_source"],
        "posture": (intel.get("sprint_verdict") or {}).get("posture", "unknown"),
        "dominant_signal_path": (intel.get("signal_path") or {}).get("dominant_signal_path", "unknown"),
        "corroborated": (intel.get("signal_path") or {}).get("is_corroborated", False),
        "is_noisy": (intel.get("signal_path") or {}).get("is_noisy", False),
        "next_pivot": (intel.get("signal_path") or {}).get("next_pivot_recommendation", "unknown"),
        "branch_verdict": (intel.get("branch_value") or {}).get("branch_verdict", "unknown"),
        "risk_score": (intel.get("correlation") or {}).get("risk_score", 0.0),
        "hypothesis_count": (intel.get("hypothesis_pack") or {}).get("hypothesis_count", 0),
        "first_action": (intel.get("sprint_verdict") or {}).get("first_action", ""),
        "confidence": (intel.get("sprint_verdict") or {}).get("confidence", ""),
        "runtime_truth_level": inp.runtime_truth_level,
        "checkpoint_zero_category": inp.ckpt_category,
        "checkpoint_zero_reason": inp.checkpoint_zero_reason,
        "observed_run_tuple": inp.observed_run_tuple,
        "canonical_sprint_owner": "runtime.sprint_entrypoint.run_sprint",
        "canonical_path_used": "run_sprint",
        "effective_source_mix": inp.src_mix_str,
        "effective_parallelism": len(inp.live_feed_urls),
        "effective_timeouts": {},
        "active_iteration_count": result.cycles_completed,
        **_extract_result_fields(result, inp.export_finish_status),
        "timing_truth": inp.timing_truth,
        **inp.acq_payload,
    }

    return ExportHandoff(
        sprint_id=ctx.sprint_id,
        scorecard=scorecard,
        top_nodes=inp.top_seed_nodes,
        phase_durations=inp.phase_durations,
        runtime_truth=inp.runtime_truth,
        execution_context={
            "query": inp.query,
            "requested_duration_s": inp.duration_s,
            "actual_duration_s": round(inp.actual_duration, 2),
            "source_count": len(inp.live_feed_urls),
            "sources": inp.live_feed_urls,
            "platform": {
                "python_version": _PLATFORM_INFO["python_version"],
                "macos_version": _PLATFORM_INFO["macos_version"],
            },
            "report_path": str(ctx.report_path),
            "git_snapshot": "unknown",
            "export_dir": str(ctx.report_path.parent),
        },
        canonical_run_summary=canonical_run_summary,
        synthesis_outcome_payload=None,
        sprint_verdict=intel.get("sprint_verdict"),
        analyst_brief=ctx.scheduler.get_analyst_brief() if ctx.scheduler else None,
        timer_events=getattr(result, "timer_events", None),
        uncertainty_flags=getattr(result, "synthesis_uncertainty_flags", None) or None,
    )


async def _run_sprint_teardown(ctx: SprintRunContext) -> None:
    """
    PHASE REFACTORING F350M-R: TEARDOWN phase - resource cleanup.

    Handles all cleanup operations via extracted helpers:
    - _teardown_power_and_tasks: Power assertion + task cancellation
    - _teardown_scheduler: Scheduler shutdown
    - _teardown_duckdb: DuckDB maintenance
    - _teardown_evidence_log: EvidenceLog teardown
    - _teardown_transports: HTTP client shutdown
    - _teardown_cleanup: Ephemeral wipe, GC, checkpoints, lock

    Args:
        ctx: SprintRunContext with all resources
    """
    await _teardown_power_and_tasks(ctx)
    await _teardown_dashboard(ctx)
    await _teardown_scheduler(ctx)
    await _teardown_duckdb(ctx)
    await _teardown_evidence_log(ctx)
    await _teardown_transports(ctx)
    await _teardown_cleanup(ctx)


async def _teardown_power_and_tasks(ctx: SprintRunContext) -> None:
    """Release power assertion and cancel orphan tasks."""
    # Release power assertion (APEX-1001)
    if ctx.power_assertion is not None:
        ctx.power_assertion.release()
        logger.info("[TEARDOWN] Power assertion released")

    # Task cancellation (F4.4)
    from hledac.universal.utils.async_helpers import cancel_scope_drain
    count = await cancel_scope_drain(timeout=5.0, label="orphan_drain")
    if count > 0:
        logger.debug("[SPRINT] Cancelled and drained %d orphan tasks", count)


async def _teardown_dashboard(ctx: SprintRunContext) -> None:
    """Finish dashboard display."""
    if ctx.dashboard is not None:
        with _fail_safe_async("warning", "dashboard.finish"):
            elapsed_s = time.monotonic() - ctx.phase_times["BOOT"]
            ctx.dashboard.finish(ctx.result, elapsed_s)


async def _teardown_scheduler(ctx: SprintRunContext) -> None:
    """Shutdown scheduler (F285 - Metal, LMDB, Hermes, transports)."""
    if ctx.scheduler is not None:
        with _fail_safe_async("debug", "scheduler.aclose"):
            await ctx.scheduler.aclose(timeout_s=10.0)


async def _teardown_duckdb(ctx: SprintRunContext) -> None:
    """DuckDB teardown maintenance (BLITZ-07/09)."""
    if ctx.store is not None:
        with _fail_safe_async("debug", "duckdb.teardown"):
            ctx.store.set_maintenance_disabled_during_active(False)
            await ctx.store.run_teardown_maintenance()
            ctx.store.set_journal_active_optimized(False)
            await ctx.store.run_journal_teardown()


async def _teardown_evidence_log(ctx: SprintRunContext) -> None:
    """Close EvidenceLog and DuckDBStore in parallel."""
    _core_close_targets: list[Any] = []
    _elog = getattr(ctx.scheduler, '_evidence_log', None)
    if _elog is not None and _elog.value is not None:
        _core_close_targets.append(_elog.value)
    if ctx.store is not None:
        _core_close_targets.append(ctx.store)

    if _core_close_targets:
        with _fail_safe_async("debug", "parallel_close.core"):
            from hledac.universal.utils.async_helpers import parallel_close
            _core_close_errors = await parallel_close(
                _core_close_targets,
                concurrency=2,
                ctx="teardown.core",
            )
            for _err in _core_close_errors:
                if _err is not None:
                    logger.debug(f"[TEARDOWN] Resource close error: {_err}")


async def _teardown_transports(ctx: SprintRunContext) -> None:
    """Close all HTTP clients in parallel."""
    with _fail_safe_async("debug", "parallel_close_async.transports"):
        from hledac.universal.utils.async_helpers import parallel_close_async
        from hledac.universal.transport.httpx_client import close_httpx_client_async
        from hledac.universal.transport.curl_cffi_runtime import close_curl_cffi_sessions_async
        from hledac.universal.fetching.public_fetcher import close_public_fetcher_sessions_async
        from hledac.universal.network.session_runtime import close_aiohttp_session_async

        _transport_close_errors = await parallel_close_async(
            [
                ("httpx", close_httpx_client_async),
                ("curl_cffi", close_curl_cffi_sessions_async),
                ("public_fetcher", close_public_fetcher_sessions_async),
                ("aiohttp", close_aiohttp_session_async),
            ],
            concurrency=4,
            ctx="teardown.transports",
        )
        failed_transports = [name for name, exc in _transport_close_errors.items() if exc is not None]
        if failed_transports:
            logger.debug(f"[TEARDOWN] transport close failures: {failed_transports}")


async def _teardown_cleanup(ctx: SprintRunContext) -> None:
    """Final cleanup: ephemeral wipe, GC, checkpoints, lock."""
    # Ephemeral state wipe (ADVERSARY-005)
    with _fail_safe_async("debug", "ephemeral_wipe"):
        from hledac.universal.security.ephemeral_wipe import EphemeralStateAnnihilator
        _wipe_result = await EphemeralStateAnnihilator().annihilate()
        if _wipe_result.get("buffers_wiped", 0) > 0 or _wipe_result.get("munlock_count", 0) > 0:
            logger.debug(f"[ADVERSARY-005] ephemeral wipe: buffers={_wipe_result['buffers_wiped']}")

    # GC cycle maintain
    with _fail_safe_async("debug", "gc_cycle_maintain"):
        await asyncio.to_thread(_memory_cycle.gc_cycle_maintain, force=False)

    # ToT checkpoint cleanup (UNIFIED-007)
    if ctx.store is not None:
        with _fail_safe_async("debug", "tot_checkpoint.cleanup"):
            from hledac.universal.coordinators.tot_checkpointer import TransactionalToTCheckpointer
            _cleanup_ckpt = TransactionalToTCheckpointer(
                sprint_id=ctx.sprint_id,
                duckdb_store=ctx.store,
                interval_s=30.0,
                lmdb_incremental=True,
                fs_fallback=True,
            )
            await _cleanup_ckpt.cleanup()
            logger.debug("[UNIFIED-007] ToT checkpoints cleaned up")

    # Final lock release
    if ctx.sprint_lock_mgr is not None:
        with _fail_safe_async("debug", "sprint_lock.release"):
            ctx.sprint_lock_mgr.release()
            logger.debug("[F266-LOCK] Released sprint lock")


# =============================================================================
# Main sprint runner
# =============================================================================


@_otel_instrumented("sprint.run", component="cli")
async def run_sprint(
    query: str,
    duration_s: float = 1800.0,
    export_dir: str = str(Path.home() / ".hledac" / "reports"),
    aggressive_mode: bool = False,
    deep_probe_enabled: bool = False,
    deep_research: bool = False,
    extreme_mode: bool = False,
    _no_communication: bool = False,
    ui_mode: bool = False,
    windup_lead_s: float | None = None,
    acquisition_profile: str | None = None,
    rl_train_mode: bool = False,
    force: bool = False,
    flags: SprintFlags | None = None,
    shutdown_event: asyncio.Event | None = None,
    resume: bool = True,
    prng_seed: int | None = None,
    replay_seed: int | None = None,
    warc_dir: str | None = None,
) -> None:
    """
    PHASE REFACTORING F350M-R: Thin orchestrator for run_sprint.
    
    This function now delegates all work to extracted phase functions:
    - _run_sprint_boot: Pre-flight, init, lock acquisition
    - _run_sprint_execute: Scheduler setup, sprint race, execution
    - _run_sprint_windup: Result processing, report generation, export
    - _run_sprint_teardown: Resource cleanup
    
    ROLE: CANONICAL SPRINT OWNER — SOLE production sprint authority.
    All report truth surfaces (canonical_run_summary, runtime_truth, timing_truth,
    checkpoint_zero_category, observed_run_tuple) are derived here.
    """
    # Create run context for all phases
    ctx = SprintRunContext()
    
    # Validate WARC directory in replay mode
    if replay_seed is not None and warc_dir is None:
        logger.warning(
            "[ULTIMATE-001] Replay mode without --warc-dir: "
            "live HTTP fetching will be used instead of WARC responses"
        )
    
    try:
        # PHASE 1: BOOT - Pre-flight, initialization, lock acquisition
        await _run_sprint_boot(
            ctx=ctx,
            query=query,
            duration_s=duration_s,
            windup_lead_s=windup_lead_s,
            flags=flags,
            force=force,
            aggressive_mode=aggressive_mode,
            resume=resume,
            prng_seed=prng_seed,
            replay_seed=replay_seed,
        )
        
        # PHASE 2: EXECUTE - Scheduler setup, sprint race, execution
        await _run_sprint_execute(
            ctx=ctx,
            query=query,
            duration_s=duration_s,
            aggressive_mode=aggressive_mode,
            deep_research=deep_research,
            extreme_mode=extreme_mode,
            acquisition_profile=acquisition_profile,
            flags=flags,
            rl_train_mode=rl_train_mode,
            ui_mode=ui_mode,
            export_dir=export_dir,
        )
        
        # PHASE 3: WINDUP - Result processing, report generation, export
        await _run_sprint_windup(
            ctx=ctx,
            query=query,
            duration_s=duration_s,
            export_dir=export_dir,
            deep_probe_enabled=deep_probe_enabled,
        )
        
    except asyncio.CancelledError:
        # Handle cancellation gracefully
        logger.info("[run_sprint] Sprint cancelled — running teardown")
        raise
    finally:
        # PHASE 4: TEARDOWN - Resource cleanup (always runs)
        await _run_sprint_teardown(ctx)



async def run_semantic_pivot(query: str, top_k: int = 10) -> None:
    """
    Sprint 8SB: Semantic pivot — ANN search for similar findings.

    Loads SemanticStore, runs semantic_pivot, prints results.
    """
    from hledac.universal.knowledge.semantic_store import SemanticStore
    from hledac.universal.paths import RAMDISK_ROOT

    lancedb_path = RAMDISK_ROOT / "lancedb"
    store = SemanticStore(db_path=lancedb_path)
    await store.initialize()

    try:
        results = await store.semantic_pivot(query, top_k=top_k)
        print(f"\n[SEMANTIC PIVOT] query: {query!r}  top_k={top_k}")
        if not results:
            print("  No results found.")
        for r in results:
            score = r.get("score", 0.0)
            src = r.get("source_type", "?")
            text = r.get("text", "")[:120]
            ts = r.get("ts", 0)
            print(f"  [{score:.3f}] {src:15} | {text}")
            if ts:
                print(f"               ts: {datetime.datetime.fromtimestamp(ts):.0f}")  # noqa: DTZ006
        print(f"\nTotal results: {len(results)}")
    finally:
        await store.close()


# --------------------------------------------------------------------------- #
# Signal handler management (module-level, no nested closures)
# --------------------------------------------------------------------------- #


class _SignalHandlerContext:
    """
    Manages signal handlers for a specific event loop and shutdown event.

    Replaces nested closure anti-pattern with explicit state management.
    """

    __slots__ = ("loop", "shutdown_event", "_prev_int", "_prev_term", "_using_add_signal_handler")

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        shutdown_event: asyncio.Event,
    ) -> None:
        self.loop = loop
        self.shutdown_event = shutdown_event
        self._prev_int: Callable[[int, Any], Any] | None = None
        self._prev_term: Callable[[int, Any], Any] | None = None
        self._using_add_signal_handler: bool = False

    def _handler(self) -> None:
        """No-arg callback for loop.add_signal_handler()."""
        logging.info("[SIGNAL] Received — cooperative shutdown")
        try:
            self.shutdown_event.set()
        except Exception:  # noqa: BLE001
            pass

    def _fallback_handler(self, signum: int, _frame: Any) -> None:
        """Two-arg handler for signal.signal() fallback."""
        sig_name = (
            getattr(signal.Signals, "SIGINT", None) and signal.Signals(signum).name
            if hasattr(signal, "Signals")
            else str(signum)
        )
        logging.info(f"[SIGNAL] Received {sig_name} — cooperative shutdown")
        try:
            if self.loop.is_running():
                self.loop.call_soon_threadsafe(self.shutdown_event.set)
            self.shutdown_event.set()
        except Exception:  # noqa: BLE001
            pass

    def install(self) -> None:
        """Install signal handlers based on platform capabilities."""
        # F350M-R ISSUE #4: Prefer loop.add_signal_handler() (Python 3.10+)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self.loop.add_signal_handler(sig, self._handler)
                self._using_add_signal_handler = True
            except (NotImplementedError, AttributeError, OSError, RuntimeError) as e:
                # NotImplementedError: signals not available in this env (e.g. some CI)
                # RuntimeError: called from non-main thread
                # AttributeError: older Python without add_signal_handler
                # OSError: system-level failure
                logging.warning(f"[SIGNAL] add_signal_handler unavailable for {sig}: {e}")
                try:
                    # Fallback to legacy signal.signal() from main thread
                    prev = signal.signal(sig, self._fallback_handler)
                    if sig == signal.SIGINT:
                        self._prev_int = prev
                    else:
                        self._prev_term = prev
                except (OSError, TypeError) as e2:
                    logging.warning(f"[SIGNAL] signal.signal() also failed for {sig}: {e2}")

        if self._using_add_signal_handler:
            logging.info("[SIGNAL] SIGINT/SIGTERM handlers installed via add_signal_handler")
        else:
            logging.info("[SIGNAL] SIGINT/SIGTERM handlers installed via signal.signal() (fallback)")

    def restore(self) -> None:
        """Restore previous signal handlers."""
        if self._using_add_signal_handler:
            # With add_signal_handler, we must remove the handler
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    self.loop.remove_signal_handler(sig)
                except (OSError, RuntimeError) as e:
                    logging.warning(f"[SIGNAL] remove_signal_handler failed for {sig}: {e}")
        else:
            # Restore previous handlers from signal.signal() fallback
            try:
                if self._prev_int is not None:
                    signal.signal(signal.SIGINT, self._prev_int)
                if self._prev_term is not None:
                    signal.signal(signal.SIGTERM, self._prev_term)
            except Exception:  # noqa: BLE001
                pass


def _install_signal_handler_for_loop(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
) -> Callable[[], None]:
    """
    Install SIGINT/SIGTERM handlers bound to a specific loop and event.

    Returns a cleanup function that restores previous signal handlers.
    Handler is idempotent, fail-soft, never calls loop.stop().

    F350M-R ISSUE #4 FIX: Uses loop.add_signal_handler() (Python 3.10+)
    for native asyncio signal handling. Falls back to signal.signal()
    for older Python or environments where add_signal_handler raises.
    """
    ctx = _SignalHandlerContext(loop, shutdown_event)
    ctx.install()
    return ctx.restore


# --------------------------------------------------------------------------- #
# Bounded task drain — single canonical implementation
# --------------------------------------------------------------------------- #


async def _cancel_all_tasks(timeout_s: float = 5.0) -> None:
    """Cancel all pending tasks and wait for them to drain — bounded.

    Bounded drain: waits up to ``timeout_s`` for tasks to honour cancellation,
    then logs any stragglers and abandons them.  On M1 8GB this prevents
    DuckDB commits / MLX eval / zstd flush (each can take 30+ s) from
    blocking shutdown indefinitely.

    Canonical location: runtime/sprint_entrypoint.py.
    Imported by core/composition_root.py and cli/parser.py.

    Args:
        timeout_s: Maximum seconds to wait for tasks to drain.  Default 5 s
            keeps total shutdown < 6 s (safety margin for the caller's
            run_until_complete wrapper).
    """
    pending = [t for t in asyncio.all_tasks() if not t.done()]
    if not pending:
        return
    for t in pending:
        t.cancel()
    # ISSUE-15: asyncio.wait(ALL_COMPLETED) → asyncio.TaskGroup
    try:
        async with asyncio.timeout(timeout_s):
            gathered = await asyncio.gather(*pending, return_exceptions=True)
            _, errors = _check_gathered(gathered)
            for err in errors:
                logger.debug('[SHUTDOWN] _drain_all_tasks: task failed: %s', err)
    except asyncio.TimeoutError:
        stragglers = [t for t in pending if not t.done()]
    else:
        stragglers = []
    for t in stragglers:
        logger.warning(
            "[SHUTDOWN] Task %s did not drain in %ss — abandoning",
            t.get_name(),
            timeout_s,
        )


async def _duckdb_init_coro(
    store: "DuckDBShadowStore",
    logger: logging.Logger,
) -> bool:
    """DuckDB async init coroutine — extracted to module level.

    Module-level placement avoids creating a new coroutine factory on every
    run_sprint() call (closure capture anti-pattern). Passes store and logger
    as explicit parameters for clarity and testability.

    Args:
        store: DuckDBShadowStore instance to initialize.
        logger: Logger for warning messages on failure.

    Returns:
        True on success, False on failure (fail-soft, caller handles retry).
    """
    try:
        await store.async_initialize()
        return True
    except Exception as _init_err:
        logger.warning(
            f"[P0-3] DuckDB pre-init failed (fail-soft, store will init on first ingest): {_init_err}"
        )
        return False


def _sync_reset_circuit_breakers(logger: logging.Logger) -> bool:
    """Reset warmup counters on all domain circuit breakers — extracted to module level.

    Module-level placement avoids creating a new thread coroutine factory on
    every run_sprint() call (closure capture anti-pattern). Passes logger
    as explicit parameter for clarity and testability.

    Args:
        logger: Logger for warning messages on failure.

    Returns:
        True if breakers were reset successfully, False otherwise.
    """
    try:
        from hledac.universal.transport.circuit_breaker import _BREAKERS
        for breaker in _BREAKERS.values():
            breaker.mark_warmup_done()
        return True
    except Exception as _exc:
        logger.warning(f"[P0-3] Circuit breaker reset failed: {_exc}")
        return False


async def _reset_circuit_breakers_async(
    logger: logging.Logger,
) -> bool:
    """Reset warmup counters on all domain circuit breakers — O(n) where n<100).

    Module-level placement avoids creating a new thread coroutine factory on
    every run_sprint() call (same anti-pattern fixed for _duckdb_init_coro).
    Includes 10s timeout to prevent blocking. Returns True if successful,
    False otherwise.

    Args:
        logger: Logger for warning messages on failure or timeout.

    Returns:
        True if breakers were reset successfully, False otherwise.
    """
    try:
        async with asyncio.timeout(10.0):
            return await asyncio.to_thread(_sync_reset_circuit_breakers, logger)
    except TimeoutError:
        logger.warning("[P0-3] Circuit breaker reset timed out after 10s — continuing")
        return False
    except asyncio.CancelledError:
        raise


def _fatal(exc: BaseException, code: int = 1) -> None:
    """
    Structured fatal-error handler. Logs _MAIN_FATAL with full traceback,
    then exits with a structured exit code.

    Exit code convention (Sprint F350M-R Exit Codes):
        0   = clean success
        1   = runtime error (unexpected)
        2   = config/validation error (e.g. windup_lead guard)
        3   = programmer error / regression (NameError, ImportError, AttributeError)
        130 = SIGINT (KeyboardInterrupt)
    """
    logger.critical("_MAIN_FATAL [exit=%d]: %s\n%s", code, exc, traceback.format_exc())
    sys.exit(code)


def _run_sprint_loop(args: argparse.Namespace) -> None:
    """
    CLI sprint wiring — routes through build_runtime() as the SOLE canonical path.
    A2: Eliminates dual bootstrap. All initialization (GC, malloc, signals,
    DuckDB, telemetry) lives in composition_root; this function only:
      1. Builds the runtime graph via build_runtime()
      2. Runs it via run_runtime()
      3. Handles exit codes

    Canonical state lives in run_sprint() (inside the task).

    ULTIMATE-001: Passes seed parameters for deterministic cognitive replay.
    """
    global _build_runtime, _run_runtime
    if _build_runtime is None:
        # A2: Lazy import to avoid circular dependency (composition_root imports
        # _cancel_all_tasks from this module at module level).
        from hledac.universal.core.composition_root import (
            build_runtime as _br,
            run_runtime as _rr,
        )
        _build_runtime = _br
        _run_runtime = _rr

    sprint_flags = SprintFlags(
        force=args.force,
        no_communication=getattr(args, "no_communication", False),
        no_stealth=getattr(args, "no_stealth", False),
        no_ghost=getattr(args, "no_ghost", False),
        no_coordination=getattr(args, "no_coordination", False),
        production=getattr(args, "production", False),
        hermes_force=getattr(args, "force_hermes", False),
        # BLITZ-12: auto-enable blitz mode for sprints ≤ 30 min (1800s).
        # In a one-shot burst, stealth anti-correlation timing is irrelevant
        # and wastes 10-50s across 100+ fetch requests.
        blitz_mode=args.duration <= 1800,
    )
    # A2: Single canonical path through build_runtime() — no duplicated
    # loop/signal/GC/malloc setup. uvloop, QoS, signals all handled inside.
    loop, sprint_task, _shutdown_event, restore_signals = _build_runtime(
        query=args.query,
        duration_s=float(args.duration),
        export_dir=args.export_dir,
        aggressive_mode=args.aggressive,
        deep_probe_enabled=args.deep_probe,
        deep_research=args.deep_research,
        extreme_mode=args.extreme,
        no_communication=getattr(args, "no_communication", False),
        ui_mode=getattr(args, "ui", False),
        windup_lead_s=None,
        acquisition_profile=args.acquisition_profile,
        rl_train_mode=args.rl_train,
        force=args.force,
        flags=sprint_flags,
        # ULTIMATE-001: Deterministic cognitive replay seed parameters
        prng_seed=getattr(args, "seed", None),
        replay_seed=getattr(args, "replay_seed", None),
        warc_dir=getattr(args, "warc_dir", None),
    )
    try:
        _run_runtime(loop, sprint_task, restore_signals)
    except SystemExit:
        raise  # never swallow sys.exit() calls


def main() -> None:
    """
    Synchronous entry point with structured exit-code handling.

    Thin wrapper around _main_dispatch(). All sprint execution paths flow
    through _main_dispatch(); main() owns the catch-all envelope and exit codes.
    """
    try:
        _main_dispatch()
    except (NameError, AttributeError, ImportError) as e:
        _fatal(e, code=3)  # programmer error / regression
    except KeyboardInterrupt:
        logger.info("[MAIN] Interrupted by user")
        sys.exit(130)  # standard SIGINT convention
    except SystemExit:
        raise  # never swallow sys.exit() calls
    except Exception as e:
        _fatal(e, code=1)  # runtime error


def _main_dispatch() -> None:
    # F265ENV: Load .env file before any ENV access
    load_dotenv()
    parser = argparse.ArgumentParser(description="Hledac Sprint 8RA Runner")
    parser.add_argument("--sprint", action="store_true", help="Run in sprint mode")
    parser.add_argument("--query", type=str, default="OSINT default query")
    parser.add_argument(
        "--duration",
        type=int,
        default=1800,
        help="Sprint duration in seconds (default: 1800 = 30min)",
    )
    parser.add_argument(
        "--export-dir",
        type=str,
        default=str(Path.home() / ".hledac" / "reports"),
    )
    parser.add_argument(
        "--ct-pivot",
        type=str,
        default=None,
        help="Run CT log pivot for a domain via crt.sh",
    )
    parser.add_argument(
        "--pivot",
        type=str,
        default=None,
        help="Sprint 8SB: semantic pivot — find similar findings via ANN search",
    )
    parser.add_argument(
        "--pivot-k",
        type=int,
        default=10,
        help="Number of results for --pivot (default: 10)",
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        help="Sprint F195B: Enable aggressive mode with 8s branch budgets",
    )
    parser.add_argument(
        "--deep-probe",
        action="store_true",
        help="Run deep probe research post-sprint (deep web, S3 buckets, IPFS)",
    )
    parser.add_argument(
        "--deep-research",
        action="store_true",
        help="F11: Run enhanced deep research advisory post-sprint",
    )
    parser.add_argument(
        "--extreme",
        action="store_true",
        help="F11: Enable EXHAUSTIVE depth for deep research (implies --deep-research)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="F221-ABORT: Override the pre-flight guard that aborts sprints whose "
        "active-window budget would be below MIN_ACTIVE_WINDOW_S=30s. "
        "Emits a [F221-FORCED] warning instead of exiting with code 2. "
        "Use only for explicit dry-runs / smoke tests where zero evidence is acceptable.",
    )
    parser.add_argument(
        "--acquisition-profile",
        type=str,
        default="default",
        choices=["default", "nonfeed_diagnostic", "deep_osint_m1"],
        help="F216B/F251D: Acquisition runtime profile (default | nonfeed_diagnostic | deep_osint_m1)",
    )
    parser.add_argument(
        "--rl-train",
        action="store_true",
        help="RL F257: Enable QMIX training mode (updates Q-network weights every 10 sprints). Default is inference-only after 124 sprint warmup.",  # noqa: E501
    )
    parser.add_argument(
        "--rl-no-train",
        action="store_true",
        help="RL F261QMIX: Force inference-only mode (overrides HLEDAC_ENABLE_RL=1). Use for production runs where Q-network must NOT be updated.",  # noqa: E501
    )
    parser.add_argument(
        "--rl-train-interval",
        type=int,
        default=None,
        help="RL F261QMIX: Override HLEDAC_RL_TRAIN_INTERVAL (default 10 sprints per QMIX training step).",
    )
    parser.add_argument(
        "--no-communication",
        action="store_true",
        help="F26X-3: Skip CommunicationLayer injection in run_sprint(). Default ON, mirroring --no-coordination opt-out contract from F26X-2.",  # noqa: E501
    )
    parser.add_argument(
        "--no-ghost",
        action="store_true",
        help="F260: Skip GhostLayer injection in run_sprint(). Default ON, mirroring --no-coordination/--no-communication opt-out contract.",  # noqa: E501
    )
    parser.add_argument(
        "--no-stealth",
        action="store_true",
        help="F260: Skip StealthLayer injection in run_sprint(). Default ON, mirroring --no-coordination/--no-communication opt-out contract.",  # noqa: E501
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="F270: Print DeepSourceRegistry catalog (curated beyond-surface sources) and exit.",
    )
    parser.add_argument(
        "--tier",
        type=str,
        default=None,
        choices=["surface", "dark", "archive", "p2p", "academic"],
        help="F270: Filter --list-sources output by source tier.",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help=(
            "F272B: Production mode. Sprint aborts with exit code 2 if the pre-run "
            "health check finds fetch_coordinator_ok=False (not_initialized). "
            "Default OFF: advisory-degraded mode continues and only logs the warning. "
            "Use for CI / orchestration where a degraded run is worse than no run."
        ),
    )
    parser.add_argument(
        "--force-hermes",
        action="store_true",
        help=(
            "F273D: Force-load Hermes3 model at sprint start even if "
            "HLEDAC_ENABLE_HERMES_SYNTHESIS != '1'. Overrides the lazy hermes gate. "
            "Result exposes hermes_model_loaded (bool) and hermes_load_reason (str) "
            "so you can verify whether the model is actually resident. M1 8GB: "
            "Hermes-3-3B-4bit ~2GB -- use only when you need synthesis output, "
            "not for routine OSINT sprints."
        ),
    )
    # ULTIMATE-001: Deterministic cognitive replay seed arguments
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "ULTIMATE-001: Explicit 64-bit PRNG seed for deterministic cognitive replay. "
            "If not provided, a random seed is generated and logged. "
            "Use this with --replay-seed <seed> --warc-dir <path> to reconstruct "
            "the identical ToT tree and synthesis path for forensic verification."
        ),
    )
    parser.add_argument(
        "--replay-seed",
        type=int,
        default=None,
        help=(
            "ULTIMATE-001: Replay mode. Takes a previously captured seed to reconstruct "
            "the exact cognitive path from a prior sprint. Requires --warc-dir pointing "
            "to the WARC archive captured during the original run."
        ),
    )
    parser.add_argument(
        "--warc-dir",
        type=str,
        default=None,
        help=(
            "ULTIMATE-001: WARC archive directory for replay mode. When combined with "
            "--replay-seed, the system replays HTTP responses from the WARC file "
            "instead of fetching live, enabling deterministic forensic replay."
        ),
    )
    args = args_with_rl_resolution = parser.parse_args()
    # F261QMIX: --rl-no-train overrides --rl-train (explicit disable wins)
    if args.rl_no_train:
        args.rl_train = False
    # F261QMIX: env-var override for train interval
    if args.rl_train_interval is not None:
        import os as _os

        _os.environ["HLEDAC_RL_TRAIN_INTERVAL"] = str(args.rl_train_interval)
    args = args_with_rl_resolution

    # NOTE: basicConfig removed — structlog already configured in __main__.py:configure_logging().
    # Adding basicConfig here would reset the root logger and conflict with structlog setup.

    # P2-SILENCE: Suppress coremltools warnings about missing native libs.
    # coremltools 8.x/9.x on py3.14 tries to dlopen libcoremlpython / libmilstoragepython
    # which are part of macOS SDK / Xcode toolchain — not bundled in py3.14 wheels.
    # Warnings are benign; coremltools falls back gracefully.
    # Only suppress the specific "Failed to load" / "Fail to import" messages.
    class _CoremlNativeLibFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if record.name == "coremltools" and record.levelno == logging.WARNING:
                msg = record.getMessage()
                if "Failed to load _ML" in msg or "Failed to load '" in msg or "Fail to import Blob" in msg:
                    return False
            return True

    _coreml_logger = logging.getLogger("coremltools")
    _coreml_logger.propagate = False
    _coreml_handler = logging.NullHandler()
    _coreml_handler.addFilter(_CoremlNativeLibFilter())
    _coreml_logger.addHandler(_coreml_handler)

    # P1E-A: Set acquisition profile env var so build_acquisition_plan picks it up
    os.environ["HLEDAC_ACQUISITION_PROFILE"] = args.acquisition_profile

    if args.list_sources:
        # F270: Print DeepSourceRegistry catalog and exit.
        from hledac.universal.discovery.deep_source_registry import (
            DeepSourceRegistry,
        )

        registry = DeepSourceRegistry()
        sources = registry.get_sources(tier=args.tier)
        print(f"DeepSourceRegistry (F270): {len(sources)} curated sources")
        if args.tier:
            print(f"  tier filter: {args.tier}")
        print("-" * 110)
        print(f"{'source_id':<18} {'tier':<10} {'transport':<10} {'data_type':<14} {'rel':<5} {'name':<30} url")
        print("-" * 110)
        for src in sorted(sources, key=lambda s: (s.source_tier, s.name)):
            print(
                f"{src.source_id:<18} "
                f"{src.source_tier:<10} "
                f"{src.transport_required:<10} "
                f"{src.data_type:<14} "
                f"{src.reliability:<5.2f} "
                f"{src.name[:28]:<30} "
                f"{src.base_url}"
            )
        print("-" * 110)
        print(f"Total: {len(sources)} sources (catalog cap: 200)")
        return

    if args.ct_pivot:
        with asyncio.Runner() as runner:
            runner.run(run_ct_pivot(args.ct_pivot))
    elif args.sprint:
        _run_sprint_loop(args)
    elif args.pivot:
        with asyncio.Runner() as runner:
            runner.run(run_semantic_pivot(args.pivot, top_k=args.pivot_k))
    else:
        print("Hledac Sprint 8RA Runner")
        print("  python -m hledac.universal.runtime.sprint_entrypoint --sprint --query '...' --duration 1800")
        print("  python -m hledac.universal.core --ct-pivot example.com")
        print("  python -m hledac.universal.core --pivot 'ransomware CVE' --pivot-k 10")


if __name__ == "__main__":
    main()
