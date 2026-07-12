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
import logging
import os
import signal
import sys
import time
import traceback
import uuid
from collections.abc import Callable
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
    from hledac.universal.runtime.scheduler_v2 import SprintScheduler as SprintScheduler

# Runtime imports — lightweight, fast-loading only
from evidence_log import EvidenceLog
from hledac.universal.core import memory_cycle as _memory_cycle  # F266-U2/U3
from hledac.universal.core.resource_governor import (
    CLEAN_SWAP_MAX_GIB,
    HARD_BLOCK_SWAP_GIB,
    sample_uma_status,
)
from hledac.universal.paths import TOR_ROOT, get_sprint_json_report_path
from hledac.universal.runtime.acquisition_strategy import (
    ACQUISITION_REPORT_SCHEMA_VERSION,
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
from hledac.universal.utils.async_helpers import safe_wait_for

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
except Exception:
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


# ── Sprint S2: SprintFlags jako msgspec.Struct (frozen) ─────────────
# Puvodne @dataclass(frozen=True, slots=True). Msgspec.Struct advantages:
#   * Kompilovany `__init__` v C -> 2-3× rychlejsi konstrukce
#   * `` -> bez GC trackingu, mensi GC tlak v pre-flight guard
#   * `frozen=True` -> instance je nemenny snapshot po konstrukci
#   * Slotless storage (Struct internally uses C-level struct) -> ~40B/instance
#
# Konvence projektu: frozen +  pro immutable DTO v hot-path
# (viz SourceWork, FeedDominanceGuardResult, LaneBudgetAllocation).


class SprintFlags(msgspec.Struct, frozen=True):
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


class AcqReportPayload(msgspec.Struct):
    """
    Schema-driven acquisition report input — mirrors SprintSchedulerResult fields
    with sensible defaults so zero defensive getattr/getattr/default is needed.

    Coverage: all 149 fields accessed as result.FIELD in the original
    _scheduler_result_acquisition_payload() hot path, PLUS the canonical
    wrapper fields returned to the caller.

    M1 8GB: msgspec.Struct uses __slots__ — ~40 bytes/instance vs ~80 for
    dataclass, no GC header, direct C-level field access.
    """

    # ── Canonical wrapper fields (returned by _scheduler_result_acquisition_payload) ──
    acquisition_report: dict[str, Any] = msgspec.field(default_factory=dict)
    acquisition_terminality_checked: bool = False
    acquisition_terminality_satisfied: bool = False
    acquisition_terminality_missing_lanes: list[str] = msgspec.field(default_factory=list)
    acquisition_terminality_report: dict[str, Any] = msgspec.field(default_factory=dict)
    source_family_outcomes: list[dict[str, Any]] = msgspec.field(default_factory=list)
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
    ct_bridge_invoked: int = 0
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
    ct_planned: int = 0
    ct_scheduled: int = 0
    ct_provider_selected: str = ""
    ct_request_attempted: int = 0
    ct_request_timeout: int = 0
    ct_raw_count: int = 0
    ct_candidates_built: int = 0
    ct_storage_attempted: int = 0
    ct_storage_accepted: int = 0
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
    doh_planned: int = 0
    doh_scheduled: int = 0
    doh_request_attempted: int = 0
    doh_domains_attempted: int = 0
    doh_raw_count: int = 0
    doh_accepted_findings: int = 0
    doh_terminal_stage: str = ""
    doh_provider_errors: list[str] = msgspec.field(default_factory=list)
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
    nonfeed_expected_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
    nonfeed_missing_expected_lanes: tuple[str, ...] = msgspec.field(default_factory=tuple)
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


def _build_sfo_list(r: AcqReportPayload) -> list[dict[str, Any]]:
    """
    Build source_family_outcomes list from AcqReportPayload.
    Direct attribute access — zero getattr, zero defensive defaults.
    """
    sfo_list: list[dict[str, Any]] = []

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


def acq_payload_to_dict(result: Any, scheduler: Any, query: str, duration_s: float) -> dict[str, Any]:
    """
    [Issue #9] Schema-driven acquisition payload.

    Replaces ~659-line _scheduler_result_acquisition_payload() triple-nested
    try/except chain with:
      1. msgspec.convert(result, AcqReportPayload) — C-level validation,
         ~50× faster than 31 getattr calls + defensive defaults.
      2. Single canonical try/except around build_acquisition_report().
      3. Direct .attribute access on AcqReportPayload — zero getattr.

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
    rg_dict = {
        "return_guard_checked": r.return_guard_checked,
        "return_guard_satisfied": r.return_guard_satisfied,
        "return_guard_block_reason": r.return_guard_block_reason,
        "return_guard_attempted_lanes": list(r.return_guard_attempted_lanes or ()),
        "return_guard_skipped_lanes": dict(r.return_guard_skipped_lanes or {}),
        "return_guard_errors": dict(r.return_guard_errors or {}),
        "return_guard_delayed_for_nonfeed": r.return_guard_delayed_for_nonfeed,
    }

    # ── 5. Windup guard observation ───────────────────────────────────────────
    wg_dict = {
        "windup_guard_call_count": r.windup_guard_call_count,
        "windup_guard_callback_supplied_count": r.windup_guard_callback_supplied_count,
        "windup_guard_callback_executed_count": r.windup_guard_callback_executed_count,
        "windup_guard_required_lanes": list(r.windup_guard_required_lanes or ()),
        "windup_guard_not_applicable": r.windup_guard_not_applicable,
        "windup_guard_last_reason": r.windup_guard_last_reason,
        "windup_guard_last_allowed": r.windup_guard_last_allowed,
        "windup_guard_callback_not_executed_reason": r.windup_guard_last_callback_not_executed_reason,
    }

    # ── 6. Prewindup barrier ─────────────────────────────────────────────────
    pwb = {
        "checked": r.prewindup_barrier_checked,
        "satisfied": r.prewindup_barrier_satisfied,
        "required_lanes": list(r.prewindup_barrier_required_lanes or ()),
        "attempted_lanes": list(r.prewindup_barrier_attempted_lanes or ()),
        "skipped_lanes": dict(r.prewindup_barrier_skipped_lanes or {}),
        "errors": dict(r.prewindup_barrier_errors or {}),
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
    cfg_profile = _safe_config_get(cfg, "acquisition_profile", None) if cfg else None
    profile_from_nd = getattr(nd_raw, "acquisition_profile", None) if nd_raw else None
    acq_effective = profile_from_nd or cfg_profile or "default"

    nd: dict[str, Any] | None = None
    if nd_raw is not None:
        nd = {
            "domain_detected": getattr(nd_raw, "domain_detected", False),
            "wallet_detected": getattr(nd_raw, "wallet_detected", False),
            "enabled_nonfeed_lanes": list(getattr(nd_raw, "enabled_nonfeed_lanes", ()) or ()),
            "disabled_nonfeed_lanes": list(getattr(nd_raw, "disabled_nonfeed_lanes", ()) or ()),
            "disabled_reasons": list(getattr(nd_raw, "disabled_reasons", ()) or ()),
            "scheduled_nonfeed_lanes": list(getattr(nd_raw, "scheduled_nonfeed_lanes", ()) or ()),
            "hardware_skipped_lanes": list(getattr(nd_raw, "hardware_skipped_lanes", ()) or ()),
            "nonfeed_execution_scheduled": getattr(nd_raw, "nonfeed_execution_scheduled", False),
            "nonfeed_execution_skip_reason": getattr(nd_raw, "nonfeed_execution_skip_reason", None),
            "acquisition_profile": getattr(nd_raw, "acquisition_profile", "default"),
            "feed_cap_reason": getattr(nd_raw, "feed_cap_reason", None),
            "nonfeed_priority_enabled": getattr(nd_raw, "nonfeed_priority_enabled", False),
            "nonfeed_profile_expected_lanes": list(getattr(nd_raw, "nonfeed_profile_expected_lanes", ()) or ()),
        }

    # ── 9. Canonical build_acquisition_report — single try/except ──────────────
    acq_report: dict[str, Any] = {}
    try:
        acq_report = build_acquisition_report(
            plan=plan,
            terminality=term_rep,
            nonfeed_plan_debug=nd,
            source_family_outcomes=sfo_list,
            return_guard=rg_dict,
            prewindup_barrier=pwb,
            scheduler_exit=se_dict,
            windup_guard_observation=wg_dict,
            query=query,
            acquisition_profile=(
                _safe_config_get(nd, "acquisition_profile", "default") if nd else (acq_effective or "default")
            ),
            feed_cap_reason=(nd.get("feed_cap_reason") if nd else None),
            nonfeed_priority_enabled=(
                nd.get("nonfeed_priority_enabled", False) if nd else (acq_effective == "nonfeed_diagnostic")
            ),
            nonfeed_profile_expected_lanes=(
                nd.get("nonfeed_profile_expected_lanes", [])
                if nd
                else (
                    ["CT", "WAYBACK", "PASSIVE_DNS", "PIVOT_EXECUTOR", "DOH"]
                    if acq_effective in ("nonfeed_diagnostic", "deep_osint_m1")
                    else []
                )
            ),
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
            ct_quarantine_samples=list(r.ct_quarantine_samples or ()),
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
            doh_provider_errors=list(r.doh_provider_errors or ()),
            doh_cache_used=r.doh_cache_used,
            # Nonfeed surface
            nonfeed_expected_lanes=list(r.nonfeed_expected_lanes or ()),
            nonfeed_missing_expected_lanes=list(r.nonfeed_missing_expected_lanes or ()),
            wayback_terminal_state=r.wayback_terminal_state,
            passive_dns_terminal_state=r.passive_dns_terminal_state,
            nonfeed_surface_complete=getattr(result, "nonfeed_surface_complete", False),
            # Pivot seeds
            pivot_seed_domains=tuple(r.pivot_seed_domains or ()),
            pivot_seed_ips=tuple(r.pivot_seed_ips or ()),
            pivot_seed_urls=tuple(r.pivot_seed_urls or ()),
            pivot_seed_hashes=tuple(r.pivot_seed_hashes or ()),
            pivot_seed_cves=tuple(r.pivot_seed_cves or ()),
            seed_context_available=bool(
                r.pivot_seed_domains
                or r.pivot_seed_ips
                or r.pivot_seed_urls
                or r.pivot_seed_hashes
                or r.pivot_seed_cves
            ),
            seed_context_propagated=bool(r.seed_context_propagated),
            lanes_unlocked_by_seed_context=list(r.lanes_unlocked_by_seed_context or ()),
            # Acquisition plan
            acquisition_plan_build_failed=r.acquisition_plan_build_failed,
            acquisition_plan_build_error_type=r.acquisition_plan_build_error_type,
            acquisition_plan_build_error=r.acquisition_plan_build_error,
            acquisition_plan_present_for_prelude=r.acquisition_plan_present_for_prelude,
            acquisition_plan_lanes_for_prelude=tuple(r.acquisition_plan_lanes_for_prelude or ()),
            acquisition_plan_enabled_lanes_for_prelude=tuple(r.acquisition_plan_enabled_lanes_for_prelude or ()),
            acquisition_plan_profile_for_prelude=r.acquisition_plan_profile_for_prelude,
            acquisition_plan_build_error_for_prelude=r.acquisition_plan_build_error_for_prelude,
            # Nonfeed prelude
            nonfeed_prelude_enabled=r.nonfeed_prelude_enabled,
            nonfeed_prelude_expected_lanes=tuple(r.nonfeed_prelude_expected_lanes or ()),
            nonfeed_prelude_attempted_lanes=tuple(r.nonfeed_prelude_attempted_lanes or ()),
            nonfeed_prelude_terminal_lanes=tuple(r.nonfeed_prelude_terminal_lanes or ()),
            nonfeed_prelude_missing_lanes=tuple(r.nonfeed_prelude_missing_lanes or ()),
            nonfeed_prelude_error_by_lane=dict(r.nonfeed_prelude_error_by_lane or {}),
            nonfeed_prelude_accepted_by_lane=dict(r.nonfeed_prelude_accepted_by_lane or {}),
            nonfeed_prelude_duration_s=float(r.nonfeed_prelude_duration_s),
            nonfeed_prelude_feed_blocked_until_complete=r.nonfeed_prelude_feed_blocked_until_complete,
        )
        # Post-processing
        acq_report["acquisition_profile_input"] = None
        acq_report["acquisition_profile_effective"] = acq_effective
        acq_report["acquisition_profile_normalized"] = False
        acq_report["budget_violations"] = r.budget_violations
        acq_report["return_guard_block_reason"] = r.return_guard_block_reason or ""
        acq_report["ct_quarantine_count"] = r.ct_quarantine_count
        acq_report["ct_quarantine_samples"] = list(r.ct_quarantine_samples or ())
        acq_report = reconcile_lane_detail_fields(acq_report)
        acq_report = complete_source_family_outcomes_from_lane_details(acq_report)
        acq_report = complete_source_family_outcomes_from_prelude(acq_report)
        if not acq_report.get("seed_context_available"):
            has_seeds = (
                r.pivot_seed_domains
                or r.pivot_seed_ips
                or r.pivot_seed_urls
                or r.pivot_seed_hashes
                or r.pivot_seed_cves
            )
            if has_seeds:
                acq_report["seed_context_available"] = True
                acq_report["seed_context_propagated"] = r.seed_context_propagated
                if not acq_report.get("seed_context_skip_reason"):
                    acq_report["seed_context_skip_reason"] = ""
            else:
                if not acq_report.get("seed_context_skip_reason"):
                    acq_report["seed_context_skip_reason"] = "no_runtime_pivot_seeds"

    except Exception as _exc:
        logger.exception(
            "[Issue9-FALLBACK] build_acquisition_report raised: %s",
            _exc,
        )
        # Single fallback path — same semantics as original triple-nested fallback
        fallback_profile = _safe_config_get(nd, "acquisition_profile", "default") if nd else "default"
        acq_report = {
            "schema_version": f"{ACQUISITION_REPORT_SCHEMA_VERSION}-fallback",
            "terminality": term_rep,
            "source_family_outcomes": sfo_list,
            "return_guard": rg_dict,
            "prewindup_barrier": pwb,
            "scheduler_exit": se_dict,
            "windup_guard_observation": wg_dict,
            "fallback_reason": f"canonical_build_failed: {_exc}",
            "acquisition_report_fallback_used": True,
            "plan": getattr(plan, "plans", None) if plan else None,
            "prelude_plan": getattr(plan, "plans", []) if plan else [],
            "required_lane_plan": term_rep.get("required_lanes", []) if term_rep else [],
            "runtime_attempted_lanes": [
                o.get("family", "") for o in sfo_list if o.get("attempted") and o.get("family")
            ],
            "effective_acquisition_plan": list(
                set(term_rep.get("required_lanes", []) if term_rep else [])
                | {o.get("family", "") for o in sfo_list if o.get("attempted") and o.get("family")}
            ),
            "plan_semantics": ("effective_runtime" if any(o.get("attempted") for o in sfo_list) else "prelude_only"),
            "nonfeed_plan_debug": nd,
            "acquisition_profile": fallback_profile,
            "feed_cap_reason": nd.get("feed_cap_reason") if nd else None,
            "nonfeed_priority_enabled": (
                nd.get("nonfeed_priority_enabled", False)
                if nd
                else bool(r.nonfeed_priority_enabled or (acq_effective == "nonfeed_diagnostic"))
            ),
            "nonfeed_profile_expected_lanes": (
                nd.get("nonfeed_profile_expected_lanes", [])
                if nd
                else list(r.nonfeed_profile_expected_lanes or ())
                or (
                    ["CT", "WAYBACK", "PASSIVE_DNS", "PIVOT_EXECUTOR", "DOH"]
                    if acq_effective == "nonfeed_diagnostic"
                    else []
                )
            ),
            # PUBLIC
            "public_terminal_stage": r.public_terminal_stage,
            "public_stage_counters": r.public_stage_counters,
            "public_discovery_empty_reason": r.public_discovery_empty_reason,
            "public_discovery_debug_reason": r.public_discovery_debug_reason,
            "public_provider_selection_debug": r.public_provider_selection_debug or {},
            "public_bootstrap_order": r.public_bootstrap_order,
            "public_bootstrap_prevented_discovery_timeout": r.public_bootstrap_prevented_discovery_timeout,
            "public_bootstrap_first_fetch_attempted": r.public_bootstrap_first_fetch_attempted,
            # CT
            "ct_provider_status": r.ct_provider_status,
            "ct_cache_used": r.ct_cache_used,
            "ct_cache_stale": r.ct_cache_stale,
            "ct_cache_age_s": r.ct_cache_age_s,
            "ct_quarantine_count": r.ct_quarantine_count,
            "ct_quarantine_samples": list(r.ct_quarantine_samples or ()),
            "ct_planned": r.ct_planned,
            "ct_scheduled": r.ct_scheduled,
            "ct_provider_selected": r.ct_provider_selected,
            "ct_request_attempted": r.ct_request_attempted,
            "ct_request_timeout": r.ct_request_timeout,
            "ct_raw_count": r.ct_raw_count,
            "ct_bridge_invoked": r.ct_bridge_invoked,
            "ct_candidates_built": r.ct_candidates_built,
            "ct_storage_attempted": r.ct_storage_attempted,
            "ct_storage_accepted": r.ct_storage_accepted,
            "ct_terminal_stage": r.ct_terminal_stage,
            "ct_prelude_missing_but_final_attempted": r.ct_prelude_missing_but_final_attempted,
            "feed_dominance_budget": getattr(plan, "feed_dominance_budget", None) if plan else None,
            "ct_bridge_rejections_count": r.ct_bridge_rejections_count,
            "ct_storage_rejected": r.ct_storage_rejected,
            "arrow_last_flush_error": r.arrow_last_flush_error,
            "arrow_batch_dropped": r.arrow_batch_dropped,
            "arrow_flush_failure_count": r.arrow_flush_failure_count,
            "prewindup_barrier_errors": r.prewindup_barrier_errors,
            "return_guard_errors": r.return_guard_errors,
            "wayback_unchanged_rejected": r.wayback_unchanged_rejected,
            "nonfeed_provider_failures": list(r.nonfeed_provider_failures or ()),
            "quality_rejection_summary_by_family": r.quality_rejection_summary_by_family,
            "duplicate_rejection_summary_by_family": r.duplicate_rejection_summary_by_family,
            "low_information_by_family": r.low_information_by_family,
            "nonfeed_expected_lanes": list(r.nonfeed_expected_lanes or ()),
            "nonfeed_missing_expected_lanes": list(r.nonfeed_missing_expected_lanes or ()),
            "wayback_terminal_state": r.wayback_terminal_state,
            "passive_dns_terminal_state": r.passive_dns_terminal_state,
            "nonfeed_surface_complete": getattr(result, "nonfeed_surface_complete", False),
            "nonfeed_candidate_ledger_summary": r.nonfeed_candidate_ledger_summary,
            # DOH
            "doh_planned": r.doh_planned,
            "doh_scheduled": r.doh_scheduled,
            "doh_request_attempted": r.doh_request_attempted,
            "doh_domains_attempted": r.doh_domains_attempted,
            "doh_raw_count": r.doh_raw_count,
            "doh_accepted_findings": r.doh_accepted_findings,
            "doh_terminal_stage": r.doh_terminal_stage,
            "doh_provider_errors": list(r.doh_provider_errors or ()),
            "doh_cache_used": r.doh_cache_used,
            # Pivot seeds
            "pivot_seed_domains": list(r.pivot_seed_domains or ()),
            "pivot_seed_ips": list(r.pivot_seed_ips or ()),
            "pivot_seed_urls": list(r.pivot_seed_urls or ()),
            "pivot_seed_hashes": list(r.pivot_seed_hashes or ()),
            "pivot_seed_cves": list(r.pivot_seed_cves or ()),
            "seed_context_available": bool(
                r.pivot_seed_domains
                or r.pivot_seed_ips
                or r.pivot_seed_urls
                or r.pivot_seed_hashes
                or r.pivot_seed_cves
            ),
            "seed_context_propagated": r.seed_context_propagated,
            "lanes_unlocked_by_seed_context": list(r.lanes_unlocked_by_seed_context or ()),
            "budget_violations": r.budget_violations,
            "return_guard_block_reason": r.return_guard_block_reason or "",
            "acquisition_plan_build_failed": r.acquisition_plan_build_failed,
            "acquisition_plan_build_error_type": r.acquisition_plan_build_error_type,
            "acquisition_plan_build_error": r.acquisition_plan_build_error,
            "acquisition_plan_present_for_prelude": r.acquisition_plan_present_for_prelude,
            "acquisition_plan_lanes_for_prelude": list(r.acquisition_plan_lanes_for_prelude or ()),
            "acquisition_plan_enabled_lanes_for_prelude": list(r.acquisition_plan_enabled_lanes_for_prelude or ()),
            "acquisition_plan_profile_for_prelude": r.acquisition_plan_profile_for_prelude,
            "acquisition_plan_build_error_for_prelude": r.acquisition_plan_build_error_for_prelude,
            # Nonfeed prelude
            "nonfeed_prelude_enabled": r.nonfeed_prelude_enabled,
            "nonfeed_prelude_expected_lanes": list(r.nonfeed_prelude_expected_lanes or ()),
            "nonfeed_prelude_attempted_lanes": list(r.nonfeed_prelude_attempted_lanes or ()),
            "nonfeed_prelude_terminal_lanes": list(r.nonfeed_prelude_terminal_lanes or ()),
            "nonfeed_prelude_missing_lanes": list(r.nonfeed_prelude_missing_lanes or ()),
            "nonfeed_prelude_error_by_lane": dict(r.nonfeed_prelude_error_by_lane or {}),
            "nonfeed_prelude_accepted_by_lane": dict(r.nonfeed_prelude_accepted_by_lane or {}),
            "nonfeed_prelude_duration_s": float(r.nonfeed_prelude_duration_s),
            "nonfeed_prelude_feed_blocked_until_complete": r.nonfeed_prelude_feed_blocked_until_complete,
            # Next seeds
            "next_seeds_consumed_count": r.next_seeds_consumed_count,
            "next_seeds_seed_source": r.next_seeds_seed_source or "",
            "next_seeds_provider_yield": r.next_seeds_provider_yield,
            "next_seeds_pivot_deepening": r.next_seeds_pivot_deepening,
            "next_seeds_query_suggestions": list(r.next_seeds_query_suggestions or ()),
            "next_seeds_skip_reason": r.next_seeds_skip_reason or "",
            "next_seeds_ioc_domains": list(r.next_seeds_ioc_domains or ()),
            "next_seeds_ioc_ips": list(r.next_seeds_ioc_ips or ()),
            "next_seeds_ioc_urls": list(r.next_seeds_ioc_urls or ()),
            "next_seeds_ioc_hashes": list(r.next_seeds_ioc_hashes or ()),
            "next_seeds_ioc_cves": list(r.next_seeds_ioc_cves or ()),
        }
        acq_report = reconcile_lane_detail_fields(acq_report)
        acq_report = complete_source_family_outcomes_from_lane_details(acq_report)
        acq_report = complete_source_family_outcomes_from_prelude(acq_report)
        if not acq_report.get("seed_context_available"):
            has_seeds = (
                r.pivot_seed_domains
                or r.pivot_seed_ips
                or r.pivot_seed_urls
                or r.pivot_seed_hashes
                or r.pivot_seed_cves
            )
            if has_seeds:
                acq_report["seed_context_available"] = True
                acq_report["seed_context_propagated"] = r.seed_context_propagated
                if not acq_report.get("seed_context_skip_reason"):
                    acq_report["seed_context_skip_reason"] = ""
            else:
                if not acq_report.get("seed_context_skip_reason"):
                    acq_report["seed_context_skip_reason"] = "no_runtime_pivot_seeds"

    # ── 10. Return canonical wrapper ─────────────────────────────────────────────
    return {
        "acquisition_report": acq_report,
        "acquisition_terminality_checked": r.acquisition_terminality_checked,
        "acquisition_terminality_satisfied": r.acquisition_terminality_satisfied,
        "acquisition_terminality_missing_lanes": list(r.acquisition_terminality_missing_lanes or ()),
        "acquisition_terminality_report": term_rep,
        "source_family_outcomes": sfo_list,
        "scheduler_exit": se_dict,
        "return_guard": rg_dict,
        "windup_guard_observation": wg_dict,
        "prewindup_barrier": pwb,
        "acquisition_prelude_checked": r.acquisition_prelude_checked,
        "acquisition_prelude_ran": r.acquisition_prelude_ran,
        "acquisition_prelude_required_lanes": list(r.acquisition_prelude_required_lanes or ()),
        "acquisition_prelude_terminal_lanes": list(r.acquisition_prelude_terminal_lanes or ()),
        "acquisition_prelude_missing_lanes": list(r.acquisition_prelude_missing_lanes or ()),
        "acquisition_prelude_skipped_lanes": dict(r.acquisition_prelude_skipped_lanes or {}),
        "acquisition_prelude_errors": dict(r.acquisition_prelude_errors or {}),
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


def _safe_config_get(config: object, key: str, default=None):
    """
    Safe attribute/dethod access for config objects that may be dict or dataclass-like.

    Fails soft — returns default for None, missing keys, or attribute errors.
    """
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _get_rust_stats() -> dict[str, Any]:
    """
    Sprint P2-B: Gather Rust extension statistics for sprint report.

    Collects available stats from hledac_rust_extensions top-level functions
    and the TelemetryAggregator singleton. Safe — any error returns empty dict.
    """
    stats: dict[str, Any] = {}
    try:
        import hledac_rust_extensions as _rust_ext  # type: ignore[import-not-found]

        # TelemetryAggregator snapshot — real-time counters/histograms/gauges
        if hasattr(_rust_ext, "create_telemetry_aggregator"):
            try:
                _agg = _rust_ext.create_telemetry_aggregator()
                stats["telemetry"] = _agg.snapshot()
            except Exception:  # noqa: BLE001
                pass

        # Memory probe stats
        if hasattr(_rust_ext, "get_process_rss_gib"):
            try:
                stats["process_rss_gib"] = _rust_ext.get_process_rss_gib()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(_rust_ext, "get_available_memory_gib"):
            try:
                stats["available_memory_gib"] = _rust_ext.get_available_memory_gib()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(_rust_ext, "memory_pressure_level"):
            try:
                stats["memory_pressure_level"] = _rust_ext.memory_pressure_level()
            except Exception:  # noqa: BLE001
                pass

        # Adaptive scheduler state
        if hasattr(_rust_ext, "get_adaptive_cpu_threads"):
            try:
                stats["adaptive_cpu_threads"] = _rust_ext.get_adaptive_cpu_threads(0)
            except Exception:  # noqa: BLE001
                pass
        if hasattr(_rust_ext, "get_adaptive_io_threads"):
            try:
                stats["adaptive_io_threads"] = _rust_ext.get_adaptive_io_threads(0)
            except Exception:  # noqa: BLE001
                pass

        # Metal availability
        if hasattr(_rust_ext, "check_metal_availability"):
            try:
                stats["metal_available"] = _rust_ext.check_metal_availability()
            except Exception:  # noqa: BLE001
                pass

        # Rust extensions version info
        if hasattr(_rust_ext, "__version__"):
            stats["version"] = getattr(_rust_ext, "__version__", "unknown")
        elif hasattr(_rust_ext, "version"):
            try:
                stats["version"] = _rust_ext.version
            except Exception:  # noqa: BLE001
                pass

    except Exception:  # noqa: BLE001
        # hledac_rust_extensions not built — fail-soft
        pass

    return stats


def _acq_payload_without_sfo(
    result: SprintSchedulerResult,
    scheduler: SprintScheduler,
    query: str,
    duration_s: float,
) -> dict:
    """Same as _scheduler_result_acquisition_payload but without source_family_outcomes top-level.

    F265-U9: source_family_outcomes lives ONLY inside acquisition_report (as a list).
    DO NOT spread it at top-level — that creates a duplicate dict-shape ghost
    alongside the canonical list-shape version inside acquisition_report.
    """
    return {
        k: v
        for k, v in _scheduler_result_acquisition_payload(result, scheduler, query, duration_s).items()
        if k != "source_family_outcomes"
    }


def _runtime_truth(
    actual_duration_s: float,
    query: str,
    duration_s: float,
    cycles_completed: int,
    cycles_started: int,
    accepted_findings: int,
    total_pattern_hits: int,
    public_accepted_findings: int,
    feed_findings: int,
    # Sprint F194A: CT findings are additive to feed/public in canonical truth
    ct_findings: int = 0,
    # F176A: Hardware pressure surfaces for smoke classification
    swap_detected: bool = False,
    uma_state: str = "ok",
    # Sprint F195B: Branch timeout telemetry
    branch_timeout_count: int = 0,
    public_branch_timed_out: bool = False,
    ct_branch_timed_out: bool = False,
) -> dict:
    """Build canonical runtime-truth record from scheduler result data."""
    is_meaningful, evidence_note = _is_meaningful_run(
        actual_duration_s,
        cycles_completed,
        cycles_started,
        accepted_findings,
        total_pattern_hits,
        swap_detected=swap_detected,
        uma_state=uma_state,
    )

    # Branch mix — dominant signal source
    # Sprint F194A: CT findings tracked as distinct branch in branch_mix
    branch_mix = {
        "feed_findings": feed_findings,
        "public_findings": public_accepted_findings,
        "ct_findings": ct_findings,
    }

    # Primary signal source label — Sprint F194A: CT findings can dominate
    if ct_findings > 0 and feed_findings == 0 and public_accepted_findings == 0:
        primary = "ct"
    elif feed_findings > 0 and public_accepted_findings == 0 and ct_findings == 0:
        primary = "feed"
    elif public_accepted_findings > 0 and feed_findings == 0 and ct_findings == 0:
        primary = "public"
    elif feed_findings > 0 and public_accepted_findings > 0 and ct_findings == 0:
        # F214-ACQ: When feed dominates (>95%) and non-feed is minimal, label as feed
        # not mixed — the signal is overwhelmingly from the feed lane.
        total_nonfeed = public_accepted_findings + ct_findings
        feed_dominance_ratio = (
            feed_findings / (feed_findings + total_nonfeed) if (feed_findings + total_nonfeed) > 0 else 1.0
        )  # noqa: E501
        if feed_dominance_ratio > 0.95:
            primary = "feed"
        else:
            primary = "mixed"
    elif ct_findings > 0 and (feed_findings > 0 or public_accepted_findings > 0):
        primary = "mixed_ct"
    else:
        primary = "none"

    return {
        "is_meaningful": is_meaningful,
        "evidence_note": evidence_note,
        "command_params": {
            "query": query,
            "requested_duration_s": duration_s,
        },
        "actual_duration_s": round(actual_duration_s, 2),
        "cycles_completed": cycles_completed,
        "cycles_started": cycles_started,
        "branch_mix": branch_mix,
        "primary_signal_source": primary,
        "total_pattern_hits": total_pattern_hits,
        "accepted_findings": accepted_findings,
        # F176A: Hardware pressure surfaces for smoke classification
        "pre_sprint_swap_detected": swap_detected,
        "pre_sprint_uma_state": uma_state,
        # Sprint F195B: Branch timeout telemetry
        "branch_timeout_count": branch_timeout_count,
        "public_branch_timed_out": public_branch_timed_out,
        "ct_branch_timed_out": ct_branch_timed_out,
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

# Sprint M218A: GC startup tuning for M1 UMA stability.
# gc.freeze() reduces GC pause variance during long sprints.
# gc.set_threshold(1000,50,50) reduces collection frequency.
# Opt-out via HLEDAC_DISABLE_GC_FREEZE=1.
_gc_configured: bool = False


def _configure_gc_for_sprint() -> dict:
    """
    Configure Python GC for sprint workload.

    Called once at sprint boot. Freezes GC to reduce pause variance on M1.
    Sets threshold to (1000, 50, 50) to reduce collection frequency.
    Opt-out via HLEDAC_DISABLE_GC_FREEZE=1.

    Returns a dict with telemetry fields.
    """
    global _gc_configured
    result = {
        "gc_freeze_attempted": False,
        "gc_freeze_applied": False,
        "gc_thresholds": None,
        "gc_freeze_error": None,
    }
    if _gc_configured:
        return result

    import gc as _gc

    result["gc_freeze_attempted"] = True
    # gc.freeze() reduces GC pause variance during long sprints.
    # F266-U4 FIX: Version guard — Python 3.14.7+ has the gilstate_tss_set fix.
    # On M1 8GB UMA this is critical for stable MLX inference latency.
    _gc_freeze_enabled = sys.version_info >= (3, 14, 7)
    if _gc_freeze_enabled:
        try:
            if hasattr(_gc, "freeze"):
                _gc.freeze()
                result["gc_freeze_applied"] = True
                logger.info("[GC] gc.freeze() applied — reduces GC pause variance")
            else:
                logger.debug("[GC] gc.freeze() not available on this Python build")
        except Exception as exc:
            result["gc_freeze_error"] = str(exc)
            logger.debug(f"[GC] gc.freeze() failed (non-fatal): {exc}")

    try:
        _gc.set_threshold(1000, 50, 50)
        result["gc_thresholds"] = (1000, 50, 50)
        logger.debug("[GC] gc.set_threshold(1000, 50, 50)")
    except Exception as exc:
        result["gc_thresholds"] = None
        logger.debug(f"[GC] set_threshold failed (non-fatal): {exc}")

    _gc_configured = True
    return result


def run_pre_sprint_checks() -> bool:
    """
    Run mandatory pre-sprint checks.

    Returns True if safe to proceed, False to abort.
    """
    checks_passed = True

    # F273G: macOS malloc pressure relief — release fragmented pages before any allocation.
    # Must run FIRST, before MLX buffers or any memory-heavy init.
    try:
        from hledac.universal.core.memory_cycle import malloc_zone_pressure_relief

        released = malloc_zone_pressure_relief()
        if released > 0:
            logger.debug("[BOOT] malloc_zone_pressure_relief released %d bytes", released)
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001  # fail-soft

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

            def _fmt(v):
                return f"{v // (1024 * 1024):.0f}MiB" if v else "N/A"

            logger.info(
                f"[BOOT] MLX buffers: cache={_fmt(status['cache_limit_bytes'])} wired={_fmt(status['wired_limit_bytes'])} configured={status['configured']}"  # noqa: E501
            )
        except Exception as exc:
            logger.warning(f"[BOOT] MLX buffer init failed: {exc}")

    # F278A: Swap tiered policy — WARNING for diagnostic tier, EXIT 2 for hard_block.
    # SSOT: core/resource_governor.py CLEAN_SWAP_MAX_GIB / DIAGNOSTIC_SWAP_MAX_GIB / HARD_BLOCK_SWAP_GIB
    s = sample_uma_status()
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


async def write_sprint_delta(
    store: DuckDBShadowStore,
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
        # Wait for store to be healthy
        for _ in range(40):
            if await store.async_healthcheck():
                break
            await asyncio.sleep(0.05)
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


async def dry_run_sprint(query: str, duration_s: float = 300.0) -> None:
    """
    Dry-run mode: validate config, check Hermes/UMA/sources, show timing plan.
    Read-only — no DuckDB writes, no real discovery, no data downloads.

    Invariant: --dry-run is read-only. Minimal side effects (writes DRY_RUN_REPORT.json only).
    """
    import socket
    from pathlib import Path

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
    issues: list[str] = []
    verdict = "OK"

    # ── 1. Config validation ─────────────────────────────────────────────────
    _WINDUP_MIN = 30.0  # noqa: N806
    _WINDUP_MAX = 180.0  # noqa: N806
    _WINDUP_RATIO = 0.30  # noqa: N806
    effective_windup = max(_WINDUP_MIN, min(_WINDUP_MAX, duration_s * _WINDUP_RATIO))
    # Synthesis budget handled separately by scheduler via hermes_budget_s (35%
    # of active window). Guard uses pure active_budget = duration - windup.
    active_budget = max(0.0, duration_s - effective_windup)

    report["windup_lead"] = effective_windup
    report["active_budget"] = active_budget

    if effective_windup >= duration_s:
        issues.append(f"windup_lead ({effective_windup:.0f}s) >= duration ({duration_s:.0f}s) — windup would never end")
        verdict = "ABORT_RECOMMENDED"
    elif active_budget <= 0:
        issues.append(f"active_budget ({active_budget:.0f}s) <= 0 for {duration_s:.0f}s sprint — no room for fetch")
        verdict = "ABORT_RECOMMENDED"

    try:
        _ = float(duration_s)
        assert duration_s > 0
    except (TypeError, ValueError, AssertionError):
        issues.append(f"duration ({duration_s}) is not a valid positive float")
        verdict = "ABORT_RECOMMENDED"

    # ── 2. Hermes3 availability check (no full load) ───────────────────────
    hermes_ok = False
    try:
        from hledac.universal.brain.model_lifecycle import get_model_lifecycle_status

        status_dict = get_model_lifecycle_status()
        hermes_ok = status_dict.get("loaded", False)  # "loaded" key from get_model_lifecycle_status()
    except Exception as e:
        issues.append(f"Hermes3 model_lifecycle check failed: {e}")
    report["hermes_available"] = hermes_ok
    if not hermes_ok:
        issues.append("Hermes3 model not loaded — synthesis will be skipped")
        if verdict == "OK":
            verdict = "OK_WITH_WARNINGS"

    # ── 3. UMA snapshot ────────────────────────────────────────────────────
    uma_gib = 0.0
    uma_state = "unknown"
    try:
        uma = sample_uma_status()
        uma_gib = getattr(uma, "system_available_gib", 0.0)
        uma_state = getattr(uma, "state", "unknown")
    except Exception as e:
        issues.append(f"UMA snapshot failed: {e}")
        verdict = "ABORT_RECOMMENDED"
    report["uma_available_gib"] = round(uma_gib, 2)
    report["uma_state"] = uma_state
    if uma_gib < 1.0:
        issues.append(f"UMA available < 1 GiB ({uma_gib:.1f}) — Hermes3 load may OOM on M1 8GB")
        if verdict == "OK":
            verdict = "OK_WITH_WARNINGS"
    if uma_state == "emergency":
        issues.append("UMA state=emergency — abort recommended")
        verdict = "ABORT_RECOMMENDED"

    # ── 4. Network probe: DNS resolve on target ─────────────────────────────
    # F267: Fixed — was blocking the event loop. Now runs in thread pool.
    try:
        target_host = query.replace("https://", "").replace("http://", "").split("/")[0].split()[0]
        await safe_wait_for(
            asyncio.to_thread(socket.gethostbyname, target_host),
            timeout=5.0,
            label="dns_resolve",
        )
        report["dns_resolve"] = {"target": target_host, "status": "ok"}
    except (TimeoutError, socket.gaierror) as e:
        issues.append(f"DNS resolve failed for '{target_host}': {e}")
        if verdict == "OK":
            verdict = "OK_WITH_WARNINGS"
    except Exception as e:
        issues.append(f"Network probe failed: {e}")

    # ── 5. Source availability check: crt.sh, CIRCL PDNS ───────────────────
    # F267: Fixed — was using blocking urllib.request in async context.
    # F4XX: Migrated from aiohttp to httpx.
    online_sources: dict[str, bool] = {"crt.sh": False, "circl_pdns": False}
    try:

        class _OnlineCheckSession:
            """Lightweight session factory for preflight checks only."""

            _session: httpx.AsyncClient | None = None

            async def get_session(self) -> httpx.AsyncClient:
                if _OnlineCheckSession._session is None or _OnlineCheckSession._session.is_closed:
                    _OnlineCheckSession._session = httpx.AsyncClient(
                        timeout=httpx.Timeout(5.0),
                        headers={"User-Agent": "curl/8.4.0"},
                    )
                return _OnlineCheckSession._session

            async def close(self) -> None:
                if _OnlineCheckSession._session and not _OnlineCheckSession._session.is_closed:
                    await _OnlineCheckSession._session.aclose()

        _checker = _OnlineCheckSession()
        try:
            session = await _checker.get_session()
            for src_name, src_url in [
                ("crt.sh", "https://crt.sh/?q=%.example.com"),
                ("circl_pdns", "https://cirolve.circl.lu/api/pdns?q=example.com"),
            ]:
                try:
                    resp = await session.head(src_url)
                    if resp.status_code < 500:
                        online_sources[src_name] = True
                except Exception:  # noqa: BLE001
                    pass
        finally:
            await _checker.close()
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001  # network check is best-effort
    report["sources_online"] = online_sources
    for src, ok in online_sources.items():
        if not ok:
            issues.append(f"{src} unreachable — may be skipped at runtime")
            if verdict == "OK":
                verdict = "OK_WITH_WARNINGS"

    # ── 6. Timing projection ───────────────────────────────────────────────
    report["sprint_timing_plan"] = {
        "duration": duration_s,
        "windup_lead": effective_windup,
        "active_budget": active_budget,
        "phases": [
            {
                "phase": "WINDUP",
                "t_start": 0.0,
                "t_end": effective_windup,
                "description": "seed, bootstrap",
            },
            {
                "phase": "ACTIVE",
                "t_start": effective_windup,
                "t_end": effective_windup + active_budget,
                "description": f"{active_budget:.0f}s available for fetch",
            },
            {
                "phase": "SYNTHESIS",
                "t_start": effective_windup + active_budget,
                "t_end": duration_s,
                "description": "synthesis + export budget",
            },
        ],
    }

    # ── Final verdict ────────────────────────────────────────────────────────
    report["issues"] = issues
    report["verdict"] = verdict

    # ── Console summary ────────────────────────────────────────────────────
    _print_dry_run_summary(report)

    # ── Write JSON report ─────────────────────────────────────────────────
    try:
        report_dir = Path.home() / ".hledac" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "DRY_RUN_REPORT.json"
        orjson_dump = orjson.dumps
        report_path.write_bytes(orjson_dump(report, option=orjson.OPT_INDENT_2))
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
# Main sprint runner
# =============================================================================


@_otel_instrumented("sprint.run", component="cli")
async def run_sprint(
    query: str,
    duration_s: float = 1800.0,
    export_dir: str = str(Path.home() / ".hledac" / "reports"),
    aggressive_mode: bool = False,
    deep_probe_enabled: bool = False,
    deep_research: bool = False,  # F11: enhanced deep research advisory
    extreme_mode: bool = False,  # F11: EXHAUSTIVE depth for deep research
    no_communication: bool = False,  # F26X-3: opt-out of CommunicationLayer injection
    ui_mode: bool = False,
    windup_lead_s: float | None = None,
    acquisition_profile: str | None = None,  # F223A: explicit profile override
    rl_train_mode: bool = False,  # RL F257: QMIX training vs inference-only
    force: bool = False,  # F221-ABORT: override zero-active-budget pre-flight guard
    flags: SprintFlags | None = None,  # F26X-3/F260 fix: layer-injection flag bundle
) -> None:
    """
    Run a full sprint lifecycle with UMA monitoring and delta reporting.
    Uses SprintScheduler.run() directly to enable compute_sprint_intelligence() access.

    ROLE: CANONICAL SPRINT OWNER — SOLE production sprint authority.
    All report truth surfaces (canonical_run_summary, runtime_truth, timing_truth,
    checkpoint_zero_category, observed_run_tuple) are derived here.
    No alternate or residual path may claim canonical_sprint_owner = "core.__main__.run_sprint".
    """
    # Sprint 8SA: Phase timing instrumentation
    _phase_times: dict[str, float] = {}
    _phase_times["BOOT"] = time.monotonic()

    # M218A: GC tuning for M1 UMA stability — runs once per process
    _gc_telemetry = _configure_gc_for_sprint()

    # F266-U3: Start background malloc pressure-relief loop. M1 8GB UMA
    # accumulates fragmentation between GCs; a 5-minute tick asks libmalloc
    # (Darwin only) to release fragmented pages back to the kernel. On
    # non-Darwin the function is a no-op. Idempotent — only one task per
    # process. Returns None if no event loop is running.
    _pressure_relief_task = _memory_cycle.start_pressure_relief_loop()

    # Pre-sprint checks
    run_pre_sprint_checks()

    # F221-ABORT: Pre-flight guard — enforce minimum active-window budget.
    # MUST run BEFORE LMDB init (DuckDBShadowStore below) to avoid orphaned
    # lock files when the config is rejected up front. Replicates logic from
    # SprintSchedulerConfig.effective_windup_lead_s so the guard rejects only
    # what the scheduler would actually treat as zero-active-budget.
    # sys.exit(2) = config error, distinguishable from exit(1) runtime failure.
    #
    # F290: Adaptive windup ratio — short sprints get smaller windup overhead.
    #   sprint <= 120s -> ratio 0.20  (windup = 20% of duration, e.g. 60s -> 12s)
    #   sprint <= 300s -> ratio 0.25  (windup = 25% of duration, e.g. 300s -> 75s)
    #   sprint > 300s  -> ratio 0.30  (windup = 30% of duration, e.g. 600s -> 180s cap)
    # Clamped [15, 180]. F289 guard then enforces windup < 80% of active window.
    # Non-MLX sprints get reduced windup via final_windup_lead_s in the scheduler.
    _F272A_WINDUP_CLAMP_MIN_S: float = 15.0  # noqa: N806 — lowered from 30 for short sprints
    _F272A_WINDUP_CLAMP_MAX_S: float = 180.0  # noqa: N806
    if float(duration_s) <= 120.0:
        _F272A_WINDUP_LEAD_FRAC: float = 0.20  # noqa: N806
    elif float(duration_s) <= 300.0:
        _F272A_WINDUP_LEAD_FRAC: float = 0.25  # noqa: N806
    else:
        _F272A_WINDUP_LEAD_FRAC: float = 0.30  # noqa: N806
    _raw_windup = float(duration_s) * _F272A_WINDUP_LEAD_FRAC
    _effective_windup_s = float(max(_F272A_WINDUP_CLAMP_MIN_S, min(_F272A_WINDUP_CLAMP_MAX_S, _raw_windup)))
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
    # This catches the case where effective_windup_s itself is too large relative
    # to the active window (e.g. sprint 60s: windup=30s → active=30s → windup IS 100% of active).
    _force_override = (flags.force if flags else False) or force
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
    # This catches the "instant windup" bug where windup_lead_s is set too high.
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
    if os.environ.get("HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED") == "1":
        if os.environ.get("PYTHON_DISABLE_REMOTE_DEBUG") != "1":
            sys.exit(
                "HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1 but PYTHON_DISABLE_REMOTE_DEBUG not set — "
                "OSINT runtime requires external debugger disabled"
            )

    # F176A: Pre-sprint UMA state capture — hardware pressure before scheduler runs.
    # This is used to classify hardware-limited smoke vs depleted query.
    _uma_pre_sprint = sample_uma_status()
    _swap_detected_pre = _uma_pre_sprint.swap_detected
    _uma_state_pre = _uma_pre_sprint.state

    # UMA baseline
    uma_baseline_gib = _uma_pre_sprint.system_used_gib

    # Sprint ID
    sprint_id = _make_sprint_id()
    _phase_times["WARMUP"] = time.monotonic()

    # F266-LOCK: Sprint-level lock — prevent two sprints with the same query from
    # running simultaneously. Uses GraphLockManager (fcntl.flock + PID header).
    # Lock is released in the finally block at the bottom of this function.
    from hledac.universal.graph.lock_manager import GraphLockManager

    _sprint_lock_mgr: GraphLockManager | None = None
    try:
        from hledac.universal.paths import get_sprint_lock_path

        _sprint_lock_path = get_sprint_lock_path(query)

        # Sprint F320: Stale-lock janitor — scan locks/ directory before acquiring.
        # Remove any lock whose owning PID is dead (crash, SIGKILL, orphaned).
        # Uses psutil.pid_exists() for cross-platform liveness check.
        try:
            import psutil

            lock_dir = _sprint_lock_path.parent
            if lock_dir.exists():
                for lock_file in lock_dir.iterdir():
                    if not lock_file.name.endswith(".lock"):
                        continue
                    try:
                        # Read PID from lock file (first 4 bytes little-endian)
                        pid_bytes = lock_file.read_bytes()
                        if len(pid_bytes) >= 4:
                            lock_pid = int.from_bytes(pid_bytes[:4], byteorder="little")
                            if not psutil.pid_exists(lock_pid):
                                lock_file.unlink()
                                logger.info(
                                    f"[F320-JANITOR] Removed stale lock: {lock_file.name} (PID={lock_pid} dead)"
                                )
                    except Exception:  # noqa: BLE001
                        pass  # best-effort
        except Exception:  # noqa: BLE001
            pass  # janitor failure is non-fatal

        _sprint_lock_mgr = GraphLockManager(str(_sprint_lock_path))
        if not _sprint_lock_mgr.acquire(timeout_s=5.0):
            _holder = _sprint_lock_mgr.holder_pid
            logger.error(
                f"[F266-LOCK-ABORT] Sprint with query '{query}' already running "
                f"(PID={_holder}). Use a different query or wait for the running sprint."
            )
            sys.exit(2)  # Config error — distinguishable from exit(1) runtime
        logger.debug(f"[F266-LOCK] Acquired sprint lock: {_sprint_lock_path}")
    except Exception as _lock_err:
        logger.warning(f"[F266-LOCK] Could not acquire sprint lock (continuing): {_lock_err}")

    # CoreML→MLX migration: CoreML sidecar removed — MLX is process-native, no subprocess.
    # DuckDB init now runs alone in ~1-2s (was sequential with 60s CoreML timeout).
    # Start both in parallel: DuckDB + (former CoreML parallel slot now eliminated).

    # P1-1 + STORAGE-DUP-003: DuckDB in-process mode — subprocess isolation removed.
    # DuckDB runs in-process via DuckDBShadowStore (M1 8GB UMA safe).
    # HLEDAC_DUCKDB_SUBPROCESS is now a no-op (subprocess path deleted).
    from hledac.universal.knowledge.duckdb_subprocess_adapter import DuckDBSubprocessAdapter

    store = DuckDBSubprocessAdapter()

    # P0-3: Pre-initialize DuckDB before sprint starts.
    # Eliminates 5–8s first-ingest penalty during active cycle.
    # Ensures connection + schema + WAL ready before first finding arrives.
    try:
        await store.async_initialize()
    except Exception as _init_err:
        logger.warning(f"[P0-3] DuckDB pre-init failed (fail-soft, store will init on first ingest): {_init_err}")

    # P2-3: Boot phase parallel init — circuit breaker reset + MLX prewarm daemon.
    # F320: prewarm_daemon.start_prewarm_if_needed() loads Hermes + MLX embeddings
    # ONCE at application startup. Subsequent sprints skip re-loading via
    # is_prewarm_done() check in SprintScheduler._prewarm_mlx_sync().
    from hledac.universal.runtime.prewarm_daemon import start_prewarm_if_needed

    start_prewarm_if_needed()

    _cb_reset_done = False

    def _reset_circuit_breakers() -> None:
        """Reset warmup counters on all domain circuit breakers — O(n) where n<100."""
        nonlocal _cb_reset_done
        try:
            from transport.circuit_breaker import _BREAKERS

            for breaker in _BREAKERS.values():
                breaker.mark_warmup_done()
            _cb_reset_done = True
        except Exception:  # noqa: BLE001
            pass

    # asyncio.to_thread offloads the sync _reset_circuit_breakers to the pool.
    _cb_reset_coro = asyncio.to_thread(_reset_circuit_breakers)

    try:
        async with asyncio.timeout(10.0):
            await _cb_reset_coro
            if not _cb_reset_done:
                logger.debug("[startup] circuit_breaker reset skipped (import failed)")
    except TimeoutError:
        logger.warning("[startup] boot circuit_breaker reset timed out after 10s — continuing")
    except asyncio.CancelledError:
        raise

    # Scheduler config
    # F221: windup_lead_s param + active-budget guard for 'default' profile
    # F228G: when windup_lead_s is not provided, compute the effective value
    # (30% of duration, clamped [30, 180]) — same formula as
    # SprintSchedulerConfig.effective_windup_lead_s. The previous default of
    # 180s for short sprints (60-90s) was LARGER than the entire sprint,
    # causing recommended_tool_mode() to return "prune" from cycle 1 and
    # triggering the empty-cycle guard immediately.
    # F290: Adaptive windup ratio — short sprints get smaller overhead to avoid
    # consuming 50-100% of the sprint budget in windup (F221/F289 abort).
    #   sprint <= 120s -> 20% (e.g. 60s -> 12s windup, 48s active)
    #   sprint <= 300s -> 25% (e.g. 300s -> 75s windup, 225s active)
    #   sprint > 300s  -> 30% (e.g. 600s -> 180s cap, 420s active)
    # Clamped [15, 180]. Matches the F221-ABORT guard formula above.
    if windup_lead_s is not None:
        _windup_lead_s = float(windup_lead_s)
    else:
        if float(duration_s) <= 120.0:
            _windup_frac = 0.20
        elif float(duration_s) <= 300.0:
            _windup_frac = 0.25
        else:
            _windup_frac = 0.30
        _raw_windup = float(duration_s) * _windup_frac
        _windup_lead_s = float(max(15.0, min(180.0, _raw_windup)))
    # F228A: Defensive normalization — benchmark profile aliases must not reach
    # acquisition_strategy as raw values. Record all three phases for telemetry.
    _acq_input = acquisition_profile
    _acq_effective = acquisition_profile
    _acq_normalized = False
    if _acq_effective == "nonfeed_diagnostic180":
        _acq_effective = "nonfeed_diagnostic"
        _acq_normalized = True
    # Check _acq_effective (not _acq_input) since _acq_effective may have been
    # normalized by the alias check above — we only want to flag truly unknown values
    if _acq_effective not in ("default", "nonfeed_diagnostic", "deep_osint_m1"):
        if _acq_input is not None and _acq_input not in ("default", "nonfeed_diagnostic", "deep_osint_m1"):
            logger.warning(
                "[F228A] Unknown acquisition_profile=%r normalized to 'default'",
                _acq_input,
            )
        _acq_effective = "default"
        _acq_normalized = True
    # Guard: smoke180 profile uses windup_lead=180s — replaced by F221-ABORT
    # pre-flight guard at the top of run_sprint(). The hard guard (MIN_ACTIVE_WINDOW_S)
    # runs before LMDB init and exits with code 2 unless --force is passed.
    # The soft warning that used to live here was redundant with the new guard
    # and is removed in F221-ABORT.
    # Propagate normalized value to scheduler and env for downstream seams
    if "HLEDAC_ACQUISITION_PROFILE" not in os.environ:
        os.environ["HLEDAC_ACQUISITION_PROFILE"] = _acq_effective or "default"
    acquisition_profile = _acq_effective or "default"

    # F273D: thread flags bundle (carries hermes_force) into SprintScheduler
    # so _prewarm_hermes_for_sprint can override HLEDAC_ENABLE_HERMES_SYNTHESIS.
    # Sprint F500I: Lazy import — SprintSchedulerConfig heavy, only needed when --sprint runs
    # STEP 4 F350M-R: Using SprintSchedulerV2 (greenfield rewrite)
    from hledac.universal.runtime.scheduler_config import SprintSchedulerConfig
    from hledac.universal.runtime.scheduler_v2 import SprintSchedulerV2

    config = SprintSchedulerConfig(
        sprint_duration_s=float(duration_s),
        windup_lead_s=_windup_lead_s,
        export_enabled=True,
        export_dir=export_dir,
        aggressive_mode=aggressive_mode,
        # Sprint F195B: 8s branch budget in aggressive mode
        branch_timeout_budget_s=8.0 if aggressive_mode else 0.0,
        # F223A: Explicit acquisition profile override
        acquisition_profile=acquisition_profile,
        # F11: Deep research advisory
        deep_research_enabled=deep_research,
        extreme_mode=extreme_mode,
    )

    scheduler = SprintSchedulerV2(config, flags=flags)

    # Sprint F11C: Wire EvidenceLog — fail-safe, M1 8GB safe
    _elog: EvidenceLog | None = None
    try:
        _elog = EvidenceLog(run_id=sprint_id, enable_persist=True)
        # FIX: EvidenceLog async initialize() MUST be called to start SQLite flush worker.
        # Without this, events go only to JSONL (sync path) but SQLite/batch write is broken.
        await _elog.initialize()
        scheduler.inject_evidence_log(_elog)

        # Sprint F11C: Record WARMUP phase event in EvidenceLog
        try:
            _elog.create_event(
                event_type="observation",
                payload={
                    "phase": "WARMUP",
                    "sprint_id": sprint_id,
                    "query": query,
                    "duration_s": duration_s,
                    "windup_lead_s": config.windup_lead_s,
                },
                confidence=1.0,
            )
        except Exception:  # noqa: BLE001
            pass  # fail-safe: evidence events never block sprint
    except Exception as _elog_err:
        logger.warning(f"[F11C] EvidenceLog wiring failed (non-fatal): {_elog_err}")

    # Sprint F153: Lifecycle receives explicit runtime params — duration authority propagated
    lifecycle = SprintLifecycleManager(
        sprint_duration_s=float(duration_s),
        windup_lead_s=config.windup_lead_s,
    )
    # Sprint F223K + RL F257: Opt-in RL feedback loop — enables quality-weighted source selection
    # RL F257: --rl-train flag enables QMIX training (Q-network weight updates every 10 sprints)
    # Sprint F500I: Lazy import — SprintPolicyManager heavy, only needed when --sprint runs
    from hledac.universal.rl.sprint_policy_manager import SprintPolicyManager

    policy_manager = SprintPolicyManager(
        enabled=True,  # F257FIX: RL is opt-out (not opt-in) — rl_train_mode gate controls training vs inference
        rl_train_mode=rl_train_mode,
    )
    scheduler.inject_policy_manager(policy_manager)

    # Sprint F26X-3: CommunicationLayer injection (advisory, default-ON, --no-communication opt-out)
    # Mirrors the F26X-2 --no-coordination contract. CommunicationLayer enables batched/bounded
    # model queries for hot-spot consumers (privacy gate, LMDB ingest, forensic fan-out).
    if not (flags.no_communication if flags else False):
        try:
            from hledac.universal.layers import get_communication_layer

            _comm_layer = get_communication_layer()
            if _comm_layer is not None:
                scheduler.inject_communication_layer(_comm_layer)
        except Exception as _e:
            logger.debug("F26X-3: CommunicationLayer injection failed (fail-soft): %s", _e)

    # Sprint F260: StealthLayer injection (advisory, default-ON, --no-stealth opt-out)
    # Mirrors --no-coordination/--no-communication contract. StealthLayer exposes
    # circuit-breaker / JA3 fingerprint rotation surfaces for advisory call sites.
    if not (flags.no_stealth if flags else False):
        try:
            from hledac.universal.layers import get_stealth_layer

            _stealth_layer = get_stealth_layer()
            if _stealth_layer is not None:
                scheduler.inject_stealth_layer(_stealth_layer)
        except Exception as _e:
            logger.debug("F260: StealthLayer injection failed (fail-soft): %s", _e)

    # Sprint F260: GhostLayer injection (advisory, default-ON, --no-ghost opt-out)
    # Mirrors --no-coordination/--no-communication contract. GhostLayer exposes
    # is_vm_environment() / force_neural_cleanup() for stealth-mode-activation pre-fetch
    # and anti-VM / neural-cleanup advisory call sites.
    if not (flags.no_ghost if flags else False):
        try:
            from hledac.universal.layers import get_ghost_layer

            _ghost_layer = get_ghost_layer()
            if _ghost_layer is not None:
                scheduler.inject_ghost_layer(_ghost_layer)
        except Exception as _e:
            logger.debug("F260: GhostLayer injection failed (fail-soft): %s", _e)

    # F26X+: SecurityCoordinator injection (advisory, research/aggressive modes)
    # Coordinates: StealthEngine, ThreatIntelligence, QuantumCrypto, ZKP.
    # Security levels: MINIMAL(1) → STANDARD(2) → HIGH(3) → MAXIMUM(4).
    if not (flags.no_stealth if flags else False):
        try:
            from hledac.universal.coordinators.security_coordinator import UniversalSecurityCoordinator

            _sec_coordinator = UniversalSecurityCoordinator(max_concurrent=3)
            if _sec_coordinator is not None:
                scheduler.inject_security_coordinator(_sec_coordinator)
        except Exception as _e:
            logger.debug("F26X+: SecurityCoordinator injection failed (fail-soft): %s", _e)

    # Sprint F200A: PrefetchOracleIntegration injection (advisory, default-ON)
    # Oracle suggests source fetch order; scheduler retains all authority.
    # All oracle calls are fail-soft -- exception or None oracle -> no-op.
    try:
        from hledac.universal.prefetch.prefetch_oracle_integration import PrefetchOracleIntegration

        _prefetch_oracle = PrefetchOracleIntegration()
        scheduler.inject_prefetch_oracle(_prefetch_oracle)
    except Exception as _e:
        logger.debug("F200A: PrefetchOracleIntegration injection failed (fail-soft): %s", _e)

    # F228F CRITICAL: inject duckdb_store before health_check so health_check
    # reads self._duckdb_store (not always None). run() also sets it from param,
    # but health_check runs BEFORE run(), so injection must happen here first.
    scheduler.inject_duckdb_store(store)

    # P3-1: Wire duckdb_store + IOC graph into oracle AFTER store is available.
    # Must happen here (before health_check) so graph is ready for predict_next_iocs.
    try:
        _oracle = getattr(scheduler, "_prefetch_oracle", None)
        if _oracle is not None:
            _oracle.inject_duckdb_store(store)
            # IOC graph: get from the graph_service singleton (same pattern as OODA wiring)
            try:
                from hledac.universal.knowledge.graph_service import _get_graph

                _ioc_graph = _get_graph()
                _oracle.inject_ioc_graph(_ioc_graph)
            except Exception as _e:
                logger.debug("P3-1: IOC graph injection failed (fail-soft): %s", _e)
    except Exception as _e:
        logger.debug("P3-1: Oracle store/graph injection failed (fail-soft): %s", _e)

    # P3-2: TemporalIOCPredictor — time-of-day pattern-based prefetch
    # P3-1: ContinuousPrefetchPipeline injection (speculative prefetch).
    # Pipeline runs advisory graph-based prefetch alongside the normal fetch cycle.
    # Pipeline is fully fail-soft: errors logged, never propagate.
    try:
        from hledac.universal.layers import get_temporal_signal_layer
        from hledac.universal.prefetch.prefetch_pipeline import ContinuousPrefetchPipeline
        from hledac.universal.prefetch.temporal_predictor import TemporalIOCPredictor

        _temporal_predictor = TemporalIOCPredictor(
            temporal_layer=get_temporal_signal_layer(),
            duckdb_store=getattr(scheduler, "_duckdb_store", None),
        )
        _prefetch_pipeline = ContinuousPrefetchPipeline(
            prefetch_oracle=_temporal_predictor,
            prefetch_cache=None,  # P3-1: no cache yet — pipeline fetches directly
            queue_depth=50,
            concurrent_fetches=3,
        )
        scheduler.inject_prefetch_pipeline(_prefetch_pipeline)
        scheduler.inject_temporal_predictor(_temporal_predictor)
    except Exception as _e:
        logger.debug("P3-1: ContinuousPrefetchPipeline injection failed (fail-soft): %s", _e)

    # Sprint F228F: Pre-run health check — verify critical dependencies
    # SprintScheduler is top architectural chokepoint (degree 398).
    # If it fails silently, nothing writes to DuckDB and nothing accumulates to graph.
    # F228F CRITICAL 3 fix: add 30s timeout to prevent indefinite hang.
    try:
        async with asyncio.timeout(30.0):
            health = await scheduler.health_check()
    except TimeoutError:
        logger.warning("[F228F] health_check timed out after 30s — continuing without pre-run check")
        health = None
    if health is not None and not health.overall_ok:
        logger.warning(f"[F228F] health_check warnings: {health.summary()}")
        # Fail-soft: log and continue — sprint will handle degraded mode gracefully
    elif health is not None:
        logger.debug(f"[F228F] health_check: {health.summary()} - real URLs from typed seed surface")

    # Sprint F272B: --production pre-flight guard.
    # In production mode, fetch_coordinator_ok=False is a hard blocker (we cannot
    # make HTTP requests, so the sprint will produce no useful findings and will
    # burn the full duration). Exit code 2 = config/preflight error per
    # docs/architecture/EXIT_CODE_CONVENTION. Default (--production absent) keeps
    # the existing fail-soft advisory-degraded behavior.
    if (flags.production if flags else False) and health is not None and not health.fetch_coordinator_ok:
        logger.error(
            f"[F272B] --production pre-flight ABORT: fetch coordinator not_initialized. "
            f"health_summary={health.summary()}. Use --no-production or fix fetch "
            f"session initialization (check TOR/I2P env vars, public_fetcher imports)."
        )
        # Exit 2 = preflight/config error (matches F221-ABORT convention).
        sys.exit(2)

    live_feed_urls = _get_live_feed_urls()

    # Sprint F193A: Instantiate CT log client for canonical pipeline
    # Sprint F500I: Lazy import — CTLogClient only needed when --sprint runs
    _ct_log_client = None
    try:
        from pathlib import Path

        from hledac.universal.intelligence.ct_log_client import CTLogClient

        _ct_cache = Path.home() / ".hledac" / "ct_cache"
        _ct_cache.mkdir(parents=True, exist_ok=True)
        _ct_log_client = CTLogClient(cache_dir=_ct_cache)
    except Exception as e:
        logger.debug(f"CT log client initialization failed: {e}")

    try:
        # Sprint F195C: Sprint dashboard — created when ui_mode=True
        _dashboard: Any = None
        if ui_mode:
            try:
                from hledac.universal.monitoring.sprint_dashboard import SprintDashboard

                _dashboard = SprintDashboard(sprint_id, query, duration_s)
                _dashboard.start()
            except Exception as e:
                logger.warning(f"Dashboard creation failed: {e}")  # fail-safe: dashboard must never block sprint

        # Sprint F195C: Progress callback for dashboard updates
        def _on_cycle(result: Any, phase: str, elapsed_s: float) -> None:
            if _dashboard is not None:
                try:
                    _dashboard.update(result, phase, elapsed_s)
                except Exception as e:
                    logger.debug(f"Dashboard update failed: {e}")

        # Run sprint via scheduler directly (enables compute_sprint_intelligence access)
        # now_monotonic=None: scheduler uses live time internally via adapter.tick()
        # F261.1: soft-fail wrapper. Any non-cancellation exception from the
        # scheduler is captured into a synthetic SprintSchedulerResult so the
        # caller (and downstream dashboard / export) still sees a structured
        # outcome rather than a 30-minute hard crash with no telemetry.
        # CancelledError (asyncio.CancelledError) is NOT caught — it must
        # propagate so cancellation semantics remain intact.
        try:
            # STEP 4 F350M-R: SprintSchedulerV2.run() signature — query only.
            # lifecycle, duckdb_store, ct_log_client wired via inject_* methods above.
            result = await scheduler.run(query)
        except Exception as _fatal_exc:
            logger.exception("SprintScheduler.run() raised; returning soft-fail result")
            try:
                from hledac.universal.runtime.scheduler_result import (
                    SprintSchedulerResult,
                )

                _sf = SprintSchedulerResult()
                _sf.scheduler_exit_path = "soft_fail"
                _sf.scheduler_exit_reason = f"{type(_fatal_exc).__name__}: {_fatal_exc}"
                result = _sf
            except Exception:
                # Last-ditch fallback: raise the original exception.
                raise
            # ISSUE-2 fix: await store.aclose() explicitly in soft-fail path
            # so WAL is flushed and connections closed even if CancelledError
            # bypasses the finally block or event loop is being torn down.
            # Fail-safe: log but never re-raise.
            try:
                await store.aclose(timeout_s=10.0)
            except asyncio.CancelledError:
                raise
            except Exception as _aclose_err:
                logger.debug(f"[ISSUE-2] store.aclose() in soft-fail path failed: {_aclose_err}")

            # F285: Also close scheduler explicitly (Metal cache, LMDB, Hermes, transports).
            # This is the canonical aclose() entry point for the graceful shutdown protocol.
            try:
                await scheduler.aclose(timeout_s=10.0)
            except asyncio.CancelledError:
                raise
            except Exception as _aclose_err:
                logger.debug(f"[F285] scheduler.aclose() in soft-fail path failed: {_aclose_err}")
        finally:
            # F266-LOCK: Always release sprint lock on exit — normal, exception, or SIGINT.
            # Idempotent: GraphLockManager.release() is safe to call multiple times.
            if _sprint_lock_mgr is not None:
                try:
                    _sprint_lock_mgr.release()
                    logger.debug("[F266-LOCK] Released sprint lock")
                except Exception as _lock_release_err:
                    logger.debug(f"[F266-LOCK] Release failed (non-fatal): {_lock_release_err}")

        # F2-3: Record DuckDB runtime mode in sprint result
        result.duckdb_mode = store.duckdb_mode

        # Sprint F150H: Pull scheduler intelligence (fail-soft, additive)
        # correlation, hypothesis_pack, signal_path, feed_verdict,
        # public_verdict, branch_value, sprint_verdict
        try:
            intel = scheduler.compute_sprint_intelligence()
        except Exception as e:
            logger.debug(f"compute_sprint_intelligence failed: {e}")

        _phase_times["WINDUP"] = time.monotonic()

        # BOOT → WINDUP: when scheduler's should_enter_windup() fires.
        # This is the active window used (NOT full scheduler runtime —
        # scheduler runs duration_s internally but windup_lead_s offsets entry).
        # e.g. requested=300s, windup_lead_s=180 → time_to_windup_s ≈ 120s (correct).
        time_to_windup_s = _phase_times["WINDUP"] - _phase_times["BOOT"]

        # F166C: actual_duration is FULL BOOT→TEARDOWN wall-clock (not time_to_windup_s).
        # time_to_windup_s was a misleading alias — it conflated pre-scheduler boot cost
        # with active window. Actual runtime for metrics/thresholds must be full wall-clock.
        # F167B fix: _phase_times["TEARDOWN"] is a timestamp; use it directly as timestamp.
        # When TEARDOWN not yet recorded (early exit), fall back to BOOT→WINDUP which IS
        # a duration stored in time_to_windup_s (not a timestamp). Guard with _phase_times["BOOT"]
        # so the arithmetic is always timestamp - timestamp = duration.
        _teardown_ts = _phase_times.get("TEARDOWN")
        actual_duration = (_teardown_ts - _phase_times["BOOT"]) if _teardown_ts is not None else time_to_windup_s

        # F166C: Pre-scheduler boot time (BOOT→WARMUP).
        # Captures import, store init, lifecycle creation overhead.
        pre_scheduler_boot_s = _phase_times.get("WARMUP", 0) - _phase_times["BOOT"]

        # F166C: Scheduler wall time (WARMUP→WINDUP).
        # Full scheduler elapsed from instantiation to windup entry.
        # If ACTIVE was reached, ACTIVE→WINDUP is part of this window (scheduled cycles ran).
        _windup_mark = _phase_times.get("WINDUP", _phase_times.get("TEARDOWN", _phase_times["BOOT"]))
        scheduler_wall_s = _windup_mark - _phase_times.get("WARMUP", _phase_times["BOOT"])

        # Sprint F11C: Record WINDUP phase event in EvidenceLog (if wired)
        try:
            if _elog is not None:
                _elog.create_event(
                    event_type="observation",
                    payload={
                        "phase": "WINDUP",
                        "sprint_id": sprint_id,
                        "query": query,
                        "time_to_windup_s": round(time_to_windup_s, 2),
                        "pre_scheduler_boot_s": round(pre_scheduler_boot_s, 2),
                        "scheduler_wall_s": round(scheduler_wall_s, 2),
                    },
                    confidence=1.0,
                )
        except Exception:  # noqa: BLE001
            pass  # fail-safe: evidence events never block sprint

        # F166C: Pre-ACTIVE starvation — scheduler already computes this;
        # __main__ re-derives for timing_truth only (not stored back to result).
        # Uses result.entered_active_at_monotonic (set by scheduler at loop guard)
        # and result.first_cycle_started_at_monotonic (set at first cycles_started += 1).

        # UMA peak
        uma_peak_gib = sample_uma_status().system_used_gib

        # Sprint F193A+F194A: CT log canonical discovery — runs once after main cycle loop.
        # In aggressive mode, CT runs in-cycle via _run_one_cycle_aggressive, so skip post-loop.
        # Sprint F194A: Persisted CT findings are additive to feed/public accepted_findings
        # in canonical sprint truth. They flow into write_sprint_delta, runtime_truth,
        # report_dict, canonical_run_summary, and export handoff.
        if not scheduler._config.aggressive_mode:
            await scheduler._run_ct_log_discovery_in_cycle(query=query, store=store)
            result.accepted_findings += result.ct_log_stored

        # Write sprint delta
        await write_sprint_delta(
            store=store,
            sprint_id=sprint_id,
            query=query,
            new_findings=result.accepted_findings,
            dedup_hits=result.duplicate_entry_hashes_skipped,
            ioc_nodes=result.unique_entry_hashes_seen,
            uma_baseline_gib=uma_baseline_gib,
            uma_peak_gib=uma_peak_gib,
            synthesis_success=result.accepted_findings > 0,
            duration_s=actual_duration,
            hits_per_source=result.hits_per_source,
        )

        # Sprint F11C: EvidenceLog teardown — fail-safe
        try:
            if _elog is not None:
                _elog.finalize()
                _elog.freeze()
        except Exception as _elog_teardown_err:
            logger.warning(f"[F11C] EvidenceLog teardown failed (non-fatal): {_elog_teardown_err}")

        _phase_times["TEARDOWN"] = time.monotonic()

        # Sprint F11C: Record TEARDOWN phase event in EvidenceLog (if wired)
        try:
            if _elog is not None:
                _elog.create_event(
                    event_type="observation",
                    payload={
                        "phase": "TEARDOWN",
                        "sprint_id": sprint_id,
                        "actual_duration_s": round(actual_duration, 2),
                        "cycles_started": result.cycles_started,
                        "cycles_completed": result.cycles_completed,
                        "accepted_findings": result.accepted_findings,
                    },
                    confidence=1.0,
                )
        except Exception:  # noqa: BLE001
            pass  # fail-safe: evidence events never block sprint

        # Sprint 8SA: Phase timing profile — uses _PHASE_ORDER from sprint_lifecycle
        phases = _PHASE_ORDER
        # F288 fix: Compute ACTUAL phase durations (end-start), not start-BOOT offsets.
        # phase_duration_seconds[PHASE] = how long the phase TOOK, not when it started.
        # For the final phase (TEARDOWN), use actual_duration as its end point.
        _phase_durations: dict[str, float] = {}
        for i, ph in enumerate(phases):
            ph_name = ph.name  # SprintPhase enum -> string key
            if ph_name in _phase_times:
                next_ph = phases[i + 1] if i + 1 < len(phases) else None
                if next_ph is not None:
                    next_name = next_ph.name
                    if next_name in _phase_times:
                        elapsed = _phase_times[next_name] - _phase_times[ph_name]
                        _phase_durations[ph_name] = round(elapsed, 2)
                        if logger.isEnabledFor(logging.INFO):
                            logger.info("[%s] %s→%s: %.1fs", sprint_id, ph_name, next_name, elapsed)
                else:
                    # Final phase (TEARDOWN): duration = actual_duration - phase_start
                    # actual_duration already computed above as _teardown_ts - _phase_times["BOOT"]
                    if ph_name in _phase_times and _teardown_ts is not None:
                        _phase_durations[ph_name] = round(_teardown_ts - _phase_times[ph_name], 2)

        # Sprint F360: Wire record_sprint_budget to MetricsRegistry — phase durations
        if _phase_durations:
            import statistics

            values = list(_phase_durations.values())
            phase_avg_ms = (sum(values) / len(values)) * 1000
            phase_p50_ms = statistics.median(values) * 1000
            values_sorted = sorted(values)
            p95_idx = max(0, int(len(values_sorted) * 0.95) - 1)
            phase_p95_ms = values_sorted[p95_idx] * 1000
            try:
                from hledac.universal.metrics_registry import get_metrics_registry

                get_metrics_registry().record_sprint_budget(
                    elapsed_ms=actual_duration * 1000,
                    remaining_ms=max(0.0, (duration_s - actual_duration) * 1000),
                    phase="TEARDOWN",
                    phase_avg_ms=phase_avg_ms,
                    phase_p50_ms=phase_p50_ms,
                    phase_p95_ms=phase_p95_ms,
                )
            except Exception:
                pass  # fail-safe: metrics never block sprint

        # --- Timing truth (Sprint F160E) -------------------------------------------
        # Canonical surfaces that distinguish:
        #   requested_duration  — what operator asked for
        #   windup_lead_s       — T-minus offset that triggers wind-down
        #   time_to_windup_s    — BOOT→WINDUP, the active window actually used
        #   time_to_teardown_s  — BOOT→TEARDOWN, full wall-clock of this run
        #   active_window_budget_s — theoretical active window (requested - windup_lead)
        #   windup_lead_observed_s — actual time between WINDUP entry and TEARDOWN
        _teardown_time = _phase_times.get("TEARDOWN", _phase_times.get("WINDUP", 0))
        windup_lead_observed_s = _teardown_time - _phase_times.get("WINDUP", 0)
        timing_truth = {
            "requested_duration_s": duration_s,
            "windup_lead_s": config.windup_lead_s,
            "time_to_windup_s": round(time_to_windup_s, 2),
            "time_to_teardown_s": round(_teardown_time - _phase_times["BOOT"], 2),
            "active_window_budget_s": round(duration_s - config.effective_windup_lead_s, 2),
            "windup_lead_observed_s": round(windup_lead_observed_s, 2),
            # F166C: Pre-scheduler boot cost (import, store init, lifecycle creation)
            "pre_scheduler_boot_s": round(pre_scheduler_boot_s, 2),
            # F166C: Scheduler wall time (WARMUP→WINDUP, full scheduler elapsed)
            "scheduler_wall_s": round(scheduler_wall_s, 2),
            # F169F: scheduler_returned_phase — derive from result state, not dict inspection
            # F167B fix: use result.entered_active_at_monotonic, NOT _phase_times["ACTIVE"]
            # (which is never set — only BOOT/WARMUP/WINDUP/TEARDOWN are written)
            "scheduler_returned_phase": ("ACTIVE" if result.entered_active_at_monotonic is not None else "entry_only"),
            # F167B fix: use result fields (first cycle STARTED not cycles_completed)
            "entered_active_truth": result.entered_active_at_monotonic is not None,
            "first_cycle_truth": result.first_cycle_started_at_monotonic is not None,
            # F166C: Pre-ACTIVE starvation — scheduler computes pre_active_starved and
            # pre_loop_blocker_reason; use directly from result (not re-derived locally).
            "pre_active_starvation": result.pre_active_starved,
            "pre_active_blocker": result.pre_loop_blocker_reason or None,
            # F166C: Full budget view for canonical runtime consumption
            "canonical_runtime_budget_view": {
                "pre_boot_s": round(pre_scheduler_boot_s, 2),
                "scheduler_elapsed_s": round(scheduler_wall_s, 2),
                "total_wallclock_s": round(actual_duration, 2),
                "budget_consumed_pct": round((actual_duration / duration_s) * 100, 1) if duration_s > 0 else 0.0,
            },
        }

        # --- Derived metrics --------------------------------------------------------
        findings_per_min = (result.accepted_findings / (actual_duration / 60.0)) if actual_duration > 0 else 0.0
        total_seen = result.unique_entry_hashes_seen + result.duplicate_entry_hashes_skipped
        dup_rate = (result.duplicate_entry_hashes_skipped / total_seen * 100) if total_seen > 0 else 0.0
        feed_fnd = result.accepted_findings - result.public_accepted_findings
        public_pct = (
            (result.public_accepted_findings / result.accepted_findings * 100) if result.accepted_findings > 0 else 0.0
        )  # noqa: E501

        # F169F: Use scheduler result fields directly — no local duplication.
        # Scheduler SprintSchedulerResult.public_backend_degraded is pre-computed.
        # DF-1: _public_backend_degraded, _feed_zero, _cross_branch_fail now
        # computed ONCE in _ckpt_category section below; verdict uses inline checks.
        _public_backend_degraded = result.public_backend_degraded

        # Source mix
        src_mix: list[str] = []
        for src, cnt in sorted(result.hits_per_source.items(), key=lambda x: x[1], reverse=True):
            src_mix.append(f"{src}={cnt}")
        src_mix_str = ", ".join(src_mix) if src_mix else "none"

        # Verdict heuristics — F176A+F169F: hardware-limited smoke is distinct from depleted query.
        # _is_hardware_limited computed once below; verdict uses same condition inline.
        _inline_hardware_limited = (
            result.accepted_findings == 0
            and result.total_pattern_hits == 0
            and result.cycles_started == 0
            and (_swap_detected_pre or _uma_state_pre in ("critical", "emergency"))
        )
        if result.aborted:
            # F178B: Aborted without findings = hard abort. Aborted WITH findings = partial signal.
            # Both share the abort modifier but the base verdict reflects signal state.
            if result.accepted_findings > 0:
                _base_verdict = (
                    "📦  NOISE-HEAVY: duplicated heavily"
                    if dup_rate > 85
                    else "🌐  PUBLIC-LED: public discovery dominated"
                    if public_pct > 60
                    else "⚖️  MIXED: public contributed meaningfully"
                    if public_pct > 25
                    else "✅  FEED-LED: feed sources strong"
                    if feed_fnd > 0
                    else "✅  SIGNAL: good feed performance"
                )
                verdict = f"⚠️  ABORTED (partial) — {_base_verdict}"
            else:
                verdict = "⚠️  ABORTED: hard stop, no signal collected"
        elif _inline_hardware_limited:
            verdict = "💾  HARDWARE-LIMITED: swap/memory pressure blocked entry"
        elif _public_backend_degraded:
            verdict = "🌐  DEGRADED: public backend/network error — check TOR/proxy/config"
        elif result.accepted_findings == 0:
            if result.public_discovered > 0:
                verdict = "🔍  NOVELTY: public found hits, feed accepted nothing"
            elif result.total_pattern_hits == 0:
                verdict = "🗿  DEPLETED: no pattern hits anywhere"
            else:
                verdict = "🤷  SILENT: pattern hits but no accepted findings"
        elif dup_rate > 85:
            verdict = "📦  NOISE-HEAVY: duplicated heavily"
        elif public_pct > 60:
            verdict = "🌐  PUBLIC-LED: public discovery dominated"
        elif public_pct > 25:
            verdict = "⚖️  MIXED: public contributed meaningfully"
        elif feed_fnd > 0:
            verdict = "✅  FEED-LED: feed sources strong"
        else:
            verdict = "✅  SIGNAL: good feed performance"

        # Next-step hint (heuristic, no new planner)
        next_hint: str
        if _inline_hardware_limited:
            next_hint = "hardware memory pressure — free RAM or restart before next run"
        elif result.accepted_findings == 0 and result.total_pattern_hits == 0:
            next_hint = "query may be too narrow — broaden terms or switch seed"
        elif dup_rate > 80:
            next_hint = "high dup rate — consider narrowing query scope"
        elif public_pct > 60:
            next_hint = "public discovery effective — let it run longer next time"
        elif public_pct < 10 and feed_fnd == 0:
            next_hint = "feed yield low — check if sources still alive (urlhaus, threatfox)"
        elif public_pct < 10 and feed_fnd > 0:
            next_hint = "feed performing — rely on feed-first, use public as supplemental"
        elif result.public_discovered > 0 and result.public_fetched == 0:
            next_hint = "public discovered but not fetched — check network/TOR"
        elif result.stop_requested:
            next_hint = "early stop triggered — lower threshold or widen query"
        else:
            next_hint = "current query and source mix working — continue as-is"

        # --- Runtime truth (smoke vs meaningful) ---------------------------------
        # [F207L] Compute CT findings: legacy ct_log_stored + new acquisition lane CT findings
        # The new acquisition lane (crtsh_adapter) is the canonical nonfeed CT path.
        # lane_ct_accepted_findings tracks new-lane CT; ct_log_stored tracks legacy CT pipeline.
        # Both paths can run in the same sprint — sum them for total CT signal.
        _lane_ct = result.lane_ct_accepted_findings or 0
        _legacy_ct = result.ct_log_stored or 0
        _total_ct = _lane_ct + _legacy_ct

        runtime_truth = _runtime_truth(
            actual_duration_s=actual_duration,
            query=query,
            duration_s=duration_s,
            cycles_completed=result.cycles_completed,
            cycles_started=result.cycles_started,
            accepted_findings=result.accepted_findings,
            total_pattern_hits=result.total_pattern_hits,
            public_accepted_findings=result.public_accepted_findings,
            feed_findings=feed_fnd,
            # Sprint F194A: CT findings additive to canonical truth accounting
            # [F207L] Sum legacy ct_log_stored + lane_ct_accepted_findings from new acquisition lanes
            ct_findings=_total_ct,
            # F176A: Hardware pressure surfaces for smoke classification
            swap_detected=_swap_detected_pre,
            uma_state=_uma_state_pre,
            # Sprint F195B: Branch timeout telemetry
            branch_timeout_count=result.branch_timeout_count,
            public_branch_timed_out=result.public_branch_timed_out,
            ct_branch_timed_out=result.ct_branch_timed_out,
        )
        is_meaningful = runtime_truth["is_meaningful"]
        evidence_note = runtime_truth["evidence_note"]

        # F164D: explicit active-runtime occurred flag — guards against
        # "windup only, no active window" drift in report layer.
        # time_to_windup_s > 0 alone is insufficient (windupLead fires immediately
        # on entry-only runs); requires is_meaningful too.
        timing_truth["active_runtime_occurred"] = is_meaningful and time_to_windup_s > 0

        # Clear separation: [SMOKE] vs [ACTIVE]
        if is_meaningful:
            logger.info(
                f"[RUNTIME TRUTH] ✅ MEANINGFUL ACTIVE RUN | {evidence_note} | "
                f"primary: {runtime_truth['primary_signal_source']} | "
                f"cycles: {result.cycles_completed}/{result.cycles_started} | "
                f"windup: {time_to_windup_s:.0f}s (budget={timing_truth['active_window_budget_s']:.0f}s)"
            )
        else:
            logger.warning(
                f"[RUNTIME TRUTH] 🚨 SMOKE ONLY | {evidence_note} | "
                f"cycles: {result.cycles_completed}/{result.cycles_started} | "
                f"windup: {time_to_windup_s:.0f}s (budget={timing_truth['active_window_budget_s']:.0f}s)"
            )

        logger.info(
            f"[SPRINT DONE] {sprint_id} | "
            f"findings: {result.accepted_findings} | "
            f"cycles: {result.cycles_completed}/{result.cycles_started} | "
            f"duplicates: {result.duplicate_entry_hashes_skipped} | "
            f"phase: {result.final_phase}"
        )
        logger.info(
            f"[SUMMARY] {verdict} | "
            f"feed={feed_fnd} public={result.public_accepted_findings}({public_pct:.0f}%) | "
            f"f/min={findings_per_min:.2f} | dup={dup_rate:.1f}% | "
            f"public: disc={result.public_discovered} fetch={result.public_fetched} "
            f"match={result.public_matched_patterns} stored={result.public_stored_findings}"
        )
        logger.info(f"[NEXT] {next_hint}")
        logger.info(f"[SOURCES] {src_mix_str}")

        # Sprint F150H: Log scheduler intelligence (visible operator signal)
        sv = intel.get("sprint_verdict") or {}
        sp = intel.get("signal_path") or {}
        corr = intel.get("correlation") or {}
        hyp = intel.get("hypothesis_pack") or {}
        if sv:
            logger.info(
                f"[INTEL] posture={sv.get('posture', '?')} | "
                f"dominant={sv.get('dominant_signal', '?')} | "
                f"corroborated={sp.get('is_corroborated', False)} | "
                f"noisy={sp.get('is_noisy', False)} | "
                f"risk={corr.get('risk_score', 0):.3f} | "
                f"hypotheses={hyp.get('hypothesis_count', 0)} | "
                f"next={sv.get('first_action', '?')[:60]}"
            )

        # Sprint F500I: Use canonical path helper (no more ad-hoc /tmp)
        report_path = get_sprint_json_report_path(sprint_id)

        # CHECKPOINT-0 additive derived fields (computed before report_dict)
        active_iterations = result.cycles_completed

        # F176A: Hardware-limited smoke detection (MUST be before runtime_truth_level)
        _is_hardware_limited = (
            not is_meaningful
            and result.cycles_started == 0
            and (_swap_detected_pre or _uma_state_pre in ("critical", "emergency"))
        )
        # F176A: Pre-active memory starvation
        _is_pre_active_mem_starved = (
            not is_meaningful
            and result.cycles_started == 0
            and result.entered_active_at_monotonic is not None
            and (_swap_detected_pre or _uma_state_pre in ("critical", "emergency", "warn"))
        )

        # F176A+E0-T4: runtime truth level taxonomy
        # F176A adds: hardware_limited_smoke, pre_active_memory_starvation, survival_active_minimal
        # E0-T4: short_signal — <180s with pattern hits but no findings.
        # 180s floor in _is_meaningful_run is exempt for hits/findings early-returns.
        # F178B: Priority order — more specific conditions must come BEFORE less specific.
        # pre_active_memory_starvation (entered ACTIVE but zero cycles with memory pressure)
        # MUST be checked before survival_active_minimal (bounded work with memory pressure).
        # hardware_limited_smoke (never entered active, zero cycles, memory pressure) comes after
        # pre_active_memory_starvation since the latter requires entered_active_at_monotonic.
        runtime_truth_level = (
            "active"
            if is_meaningful and result.accepted_findings > 0
            else "pre_active_memory_starvation"
            if _is_pre_active_mem_starved
            else "survival_active_minimal"
            if is_meaningful and _uma_state_pre in ("warn", "critical", "emergency")
            else "hardware_limited_smoke"
            if _is_hardware_limited
            else "short_signal"
            if is_meaningful and result.total_pattern_hits > 0
            else "meaningful_empty"
            if is_meaningful
            else "smoke"
        )

        # Sprint F162D: observed_run_tuple must be deterministic — no verdict string
        # (verdict is heuristic and non-reproducible across identical runs).
        # Canonical components: query-truncated, duration, iterations, source-mix, truth-level.
        observed_run_tuple = (
            query[:40] if len(query) > 40 else query,
            round(actual_duration, 1),
            active_iterations,
            src_mix_str,
            runtime_truth_level,
        )

        # CHECKPOINT-0 taxonomy (Sprint F155 + E0-T4 + F163C + F164D + F169F + F189A)
        # Disjoint machine-readable buckets — report layer must not conflate these.
        # Bucket set:
        #   signal_reaches_findings           — findings accepted
        #   pre_active_memory_starvation      — F176A: entered ACTIVE but zero cycles with memory pressure
        #   survival_active_minimal           — F176A: bounded ACTIVE work under memory pressure
        #   hardware_limited_smoke           — F176A: zero cycles + swap/pressure (hardware, not query failure)
        #   public_backend_degraded           — F169F: public branch backend error (NetworkProxyError, HTTP errors)
        #   degraded_public_blocker           — public branch error (legacy, non-backend errors)
        #   meaningful_empty_run              — F169F+F189A: meaningful query, zero pattern hits, no findings
        #   feed_ingress_blocker              — F169F: feed zero AND public discovered some signal
        #   feed_source_inaccessible          — F169F: feed failed AND total hits=0 AND no infra error
        #   true_depleted_query               — F169F: query vocabulary matched but nothing accepted
        #   short_signal                      — F189A: meaningful query with hits but no accepted findings
        #   cross_branch_source_inaccessible  — F169F: cross-branch sources failed, feed/public accessible
        #   windup_export_fail_soft           — windup fired on zero-findings run
        # Priority: findings > survival > hardware_limited > pre_active_mem > public_backend >
        #   degraded > meaningful_empty > feed_ingress > feed_source_inaccessible >
        #   true_depleted > short_signal > cross_branch > windup > depleted
        # NOTE (F189A): meaningful_empty_run moved BEFORE _feed_zero guards because it requires
        #   is_meaningful=True (query had runtime/hits evidence) while feed_source_inaccessible
        #   describes a feed infrastructure failure. short_signal moved BEFORE true_depleted_query
        #   because it requires is_meaningful=True (distinct from true_depleted_query's zero-findings
        #   verdict which doesn't distinguish meaningful vs non-meaningful query execution).
        # F192A DF-1/2/3: Use scheduler result fields directly — eliminated duplicate
        # _public_backend_degraded, _feed_zero, _cross_branch_fail local computations.
        _public_backend = result.public_backend_degraded
        _feed_zero_check = result.accepted_findings == 0 and feed_fnd == 0
        _cross_branch_fail_check = (
            result.accepted_findings == 0
            and result.total_pattern_hits > 0
            and not _public_backend
            and not result.public_error
        )
        _ckpt_category = (
            "signal_reaches_findings"
            if result.accepted_findings > 0
            # F176A: Pre-active memory starvation — entered ACTIVE but zero cycles started
            # under memory pressure. MUST come before survival_active_minimal.
            else "pre_active_memory_starvation"
            if _is_pre_active_mem_starved
            # F176A: Survival minimal active — bounded work under memory pressure
            else "survival_active_minimal"
            if is_meaningful and _uma_state_pre in ("warn", "critical", "emergency")
            # F176A: Hardware-limited smoke — zero cycles, hardware pressure
            else "hardware_limited_smoke"
            if _is_hardware_limited
            # F169F: explicit backend degraded first (httpx/network errors)
            else "public_backend_degraded"
            if _public_backend
            # F169F: degraded_public_blocker (non-backend public errors)
            else "degraded_public_blocker"
            if result.public_error
            # F189A: meaningful_empty_run BEFORE _feed_zero_check guards — meaningful query with zero hits
            # is a distinct bucket from feed_source_inaccessible (feed infrastructure failure).
            else "meaningful_empty_run"
            if is_meaningful and result.total_pattern_hits == 0 and result.accepted_findings == 0
            # F169F: feed_ingress_blocker — feed zero but public found signal
            else "feed_ingress_blocker"
            if _feed_zero_check and result.public_discovered > 0
            # F169F: feed source inaccessible — feed failed AND total hits=0 AND no infra error
            else "feed_source_inaccessible"
            if _feed_zero_check and result.total_pattern_hits == 0 and not result.public_error
            # F189A: short_signal BEFORE true_depleted_query — short_signal requires is_meaningful=True
            # (query had real runtime/hits evidence) while true_depleted_query is broader.
            else "short_signal"
            if is_meaningful and result.total_pattern_hits > 0 and result.accepted_findings == 0
            # F169F: true depleted query — hits seen but pattern matched nothing accepted
            else "true_depleted_query"
            if result.accepted_findings == 0 and result.total_pattern_hits > 0 and not _public_backend
            # F169F: cross-branch source inaccessible — hits seen but blocked by source-level failure
            else "cross_branch_source_inaccessible"
            if _cross_branch_fail_check
            else "windup_export_fail_soft"
            if result.accepted_findings == 0 and _phase_times.get("WINDUP", 0) > 0 and is_meaningful
            else "depleted"
        )
        # F176A+F169F+F190A reason chain — machine-readable, mutually exclusive.
        # F190A: chain order aligned with _ckpt_category (F189A fixes propagated to reason chain):
        #   1. meaningful_empty_run BEFORE feed_ingress_blocker/feed_source_inaccessible
        #   2. short_signal_no_findings BEFORE true_depleted_query:hits_without_acceptance
        _checkpoint_zero_reason = (
            # F176A: Hardware-limited smoke — evidence_note already has hardware_limited_smoke text
            evidence_note
            if _is_hardware_limited
            # F176A: Pre-active memory starvation
            else "pre_active_memory_starvation"
            if _is_pre_active_mem_starved
            else evidence_note
            if not is_meaningful
            else "signal_reaches_findings"
            if result.accepted_findings > 0
            # F169F: backend degraded — httpx/network errors
            else f"public_backend_degraded:{result.public_error}"
            if _public_backend
            else f"degraded_public_branch_blocked:{result.public_error}"
            if result.public_error
            # F190A: meaningful_empty_run BEFORE feed guards (aligns with _ckpt_category F189A order)
            else "meaningful_empty_run"
            if is_meaningful and result.total_pattern_hits == 0 and result.accepted_findings == 0
            # F169F: feed_ingress_blocker (meaningful=False, public found signal)
            else f"feed_ingress_blocker:{result.public_discovered}"
            if result.accepted_findings == 0 and feed_fnd == 0 and result.public_discovered > 0
            # F169F: feed source inaccessible
            else "feed_source_inaccessible"
            if result.accepted_findings == 0 and result.total_pattern_hits == 0 and not result.public_error
            # F190A: short_signal_no_findings BEFORE true_depleted_query (aligns with _ckpt_category F189A order)
            else "short_signal_no_findings"
            if is_meaningful and result.total_pattern_hits > 0
            # F169F: true depleted query — hits seen but nothing accepted, no infra error
            else "true_depleted_query:hits_without_acceptance"
            if result.accepted_findings == 0 and result.total_pattern_hits > 0 and not _public_backend
            else "cross_branch_source_inaccessible"
            if _cross_branch_fail_check
            else "depleted_no_pattern_hits"
        )
        _export_finish_status = (
            "finished"
            if result.final_phase in ("EXPORT", "TEARDOWN") and result.accepted_findings > 0 and not result.aborted
            else "aborted"
            if result.aborted
            else "empty_run"
            if result.accepted_findings == 0
            else "unknown"
        )

        report_dict = {
            "sprint_id": sprint_id,
            "query": query,
            "duration_s": duration_s,
            "actual_duration_s": actual_duration,
            "accepted_findings": result.accepted_findings,
            "feed_findings": feed_fnd,
            "public_accepted_findings": result.public_accepted_findings,
            "public_discovered": result.public_discovered,
            "public_fetched": result.public_fetched,
            "public_matched_patterns": result.public_matched_patterns,
            "public_stored_findings": result.public_stored_findings,
            "public_error": result.public_error,
            # Sprint F193A+F194A: CT log canonical discovery — additive to sprint truth
            "ct_log_discovered": result.ct_log_discovered,
            "ct_log_stored": result.ct_log_stored,
            "ct_log_accepted_findings": result.ct_log_accepted_findings,
            "ct_log_error": result.ct_log_error,
            "cycles_completed": result.cycles_completed,
            "cycles_started": result.cycles_started,
            "unique_entry_hashes_seen": result.unique_entry_hashes_seen,
            "duplicate_entry_hashes_skipped": result.duplicate_entry_hashes_skipped,
            "total_pattern_hits": result.total_pattern_hits,
            "dup_rate_pct": round(dup_rate, 2),
            "findings_per_min": round(findings_per_min, 2),
            "final_phase": result.final_phase,
            "aborted": result.aborted,
            "abort_reason": result.abort_reason,
            "stop_requested": result.stop_requested,
            "entries_per_source": result.entries_per_source,
            "hits_per_source": result.hits_per_source,
            "export_paths": result.export_paths,
            "uma_peak_gib": uma_peak_gib - uma_baseline_gib,
            "synthesis_success": result.accepted_findings > 0,
            "verdict": verdict,
            "next_hint": next_hint,
            "phase_timing": _phase_durations,
            "runtime_truth": runtime_truth,
            # Sprint F150H: Scheduler intelligence propagated fail-soft (additive)
            "correlation_summary": intel.get("correlation"),
            "hypothesis_pack_summary": intel.get("hypothesis_pack"),
            "signal_path": intel.get("signal_path"),
            "feed_verdict": intel.get("feed_verdict"),
            "public_verdict": intel.get("public_verdict"),
            "branch_value": intel.get("branch_value"),
            "sprint_verdict": intel.get("sprint_verdict"),
            # Sprint F500I: Empirical run boundary — reproducible tuple
            "execution_context": {
                "query": query,
                "requested_duration_s": duration_s,
                "actual_duration_s": round(actual_duration, 2),
                "source_count": len(live_feed_urls),
                "sources": live_feed_urls,
                "platform": {
                    "python_version": __import__("sys").version.split()[0],
                    "macos_version": __import__("platform").mac_ver()[0] or "unknown",
                },
                "report_path": str(report_path),
                "git_snapshot": "unknown",
                "export_dir": export_dir,
            },
            # Sprint F150H+F206S: Canonical operator summary — built ONCE, used in both
            # report_dict and handoff. Acquisition payload spread additively on top so
            # any canonical_run_summary fields also in _acq_payload are overwritten
            # with acquisition truth (correct: acquisition fields should take precedence).
            "canonical_run_summary": {
                "meaningful": runtime_truth["is_meaningful"],
                "primary_signal": runtime_truth["primary_signal_source"],
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
                "runtime_truth_level": runtime_truth_level,
                "checkpoint_zero_category": _ckpt_category,
                "checkpoint_zero_reason": _checkpoint_zero_reason,
                "observed_run_tuple": observed_run_tuple,
                "canonical_sprint_owner": "core.__main__.run_sprint",
                "canonical_path_used": "run_sprint",
                "effective_source_mix": src_mix_str,
                "effective_parallelism": len(live_feed_urls),
                "effective_timeouts": {},
                "active_iteration_count": active_iterations,
                # [F235] cycles_started/completed mirror runtime truth from scheduler result
                "cycles_started": result.cycles_started,
                "cycles_completed": result.cycles_completed,
                "pre_loop_elapsed_s": result.pre_loop_elapsed_s,
                "pre_loop_blocker_reason": result.pre_loop_blocker_reason,
                "pre_active_starvation": result.pre_active_starved,
                "export_finish_layer_status": _export_finish_status,
                "public_error": result.public_error,
                "ct_log_discovered": result.ct_log_discovered,
                "ct_log_stored": result.ct_log_stored,
                "ct_log_accepted_findings": result.ct_log_accepted_findings,
                "cc_archive_injected": result.cc_archive_injected,
                "academic_findings_count": result.academic_findings_count,
                "timing_truth": timing_truth,
                # Sprint F215D: Early exit semantics
                "early_exit_class": getattr(result, "early_exit_class", ""),
                "early_exit_reason": getattr(result, "early_exit_reason", ""),
                "requested_duration_s": duration_s,
                "actual_duration_s": round(actual_duration, 2),
                "elapsed_pct": round((actual_duration / duration_s) * 100, 1) if duration_s > 0 else 0.0,
                "active_window_budget_s": timing_truth["active_window_budget_s"],
                "active_window_elapsed_s": timing_truth["time_to_windup_s"],
                # G-3: Governor telemetry for hardware_critical lane gating diagnostics
                # Compare with pre_sprint_uma_state (line 1002) to detect runtime divergence
                "governor_uma_state": getattr(result, "governor_uma_state", ""),
                "governor_system_used_gib": getattr(result, "governor_system_used_gib", 0.0),
                "governor_swap_detected": getattr(result, "governor_swap_detected", False),
                "governor_io_only": getattr(result, "governor_io_only", False),
            },
            # [F208I-A] Acquisition terminality and report truth — pure, fail-soft.
            # Spread on top of canonical_run_summary so acquisition fields take precedence.
            # F265-U9: Exclude source_family_outcomes from top-level spread — it lives ONLY
            # inside acquisition_report (as a list). Having it at top-level creates a duplicate
            # of acquisition_report["source_family_outcomes"] with an incompatible dict shape.
            **_acq_payload_without_sfo(result, scheduler, query, duration_s),
            "timing_truth": timing_truth,
            # Sprint M218A: GC startup tuning telemetry
            "gc_telemetry": _gc_telemetry,
            # Sprint F217B: Nonfeed mission controller telemetry
            "nonfeed_mission_active": getattr(result, "nonfeed_mission_active", False),
            "nonfeed_required_families": getattr(result, "nonfeed_required_families", ()),
            "nonfeed_optional_families": getattr(result, "nonfeed_optional_families", ()),
            "nonfeed_family_status": getattr(result, "nonfeed_family_status", {}),
            "nonfeed_all_required_terminal": getattr(result, "nonfeed_all_required_terminal", False),
            "nonfeed_any_accepted": getattr(result, "nonfeed_any_accepted", False),
            "nonfeed_provider_failures": getattr(result, "nonfeed_provider_failures", ()),
            "nonfeed_memory_skips": getattr(result, "nonfeed_memory_skips", ()),
            "nonfeed_mission_exit_reason": getattr(result, "nonfeed_mission_exit_reason", ""),
            # Sprint P2-B: DuckDB store telemetry
            "duckdb_stats": getattr(store, "get_stats", lambda: {})(),
            # Sprint P2-B: Rust extensions telemetry — safe, fail-soft
            "rust_extensions": _get_rust_stats(),
            # Sprint F11C: EvidenceLog manifest summary (if wired)
            "evidence_manifest": (
                {
                    "total_count": _elog.size,
                    "ram_size": _elog.ram_size,
                    "persist_path": str(_elog.persist_path) if _elog.persist_path else None,
                }
                if _elog is not None
                else {"total_count": 0, "ram_size": 0, "persist_path": None, "note": "elog_not_wired"}
            ),
        }
        report_path.write_bytes(orjson.dumps(report_dict, option=orjson.OPT_INDENT_2))
        logger.info(f"[REPORT] {report_path}")

        # Sprint F151D: Wire existing exporter seam over already-computed truth surfaces.
        # Reuse: ExportHandoff, ensure_export_handoff, store.get_top_seed_nodes(),
        # intel (correlation/hypothesis_pack/signal_path/feed_verdict/
        # public_verdict/branch_value/sprint_verdict), runtime_truth, canonical_run_summary.
        # Additive + fail-soft only — exporter failure does not crash sprint.
        try:
            from hledac.universal.project_types import ExportHandoff

            top_seed_nodes: list = []
            try:
                top_seed_nodes = store.get_top_seed_nodes(n=5) if store else []
            except Exception as e:
                logger.debug(f"get_top_seed_nodes failed: {e}")

            # Sprint F155: Determine handoff enrichment level (canonical_run_summary built inline)
            _handoff_enriched = bool(runtime_truth and intel)

            # [F208J-B] Compute acquisition payload BEFORE ExportHandoff construction
            # so it can be spread into both scorecard and canonical_run_summary.
            # This ensures acquisition truth enters the actual ExportHandoff passed to export_sprint(),
            # not just the local report_dict that was written to disk.
            _acq_payload = _scheduler_result_acquisition_payload(result, scheduler, query, duration_s)

            handoff = ExportHandoff(
                sprint_id=sprint_id,
                scorecard={
                    "synthesis_engine_used": "hermes3",
                    "gnn_predicted_links": 0,
                    "top_graph_nodes": top_seed_nodes,
                    "phase_duration_seconds": _phase_durations,
                    # [F223D] runtime_accepted_findings — full truth from all lanes at windup time.
                    # F265B fix: use result.accepted_findings + result.public_accepted_findings
                    # since PUBLIC findings are tracked separately from FEED findings.
                    "runtime_accepted_findings": (result.accepted_findings or 0)
                    + (result.public_accepted_findings or 0),
                    # F220F: findings_per_minute — computed from all-lanes total / active window.
                    # PVS uses scorecard.findings_per_minute directly; adding here ensures PVS
                    # never shows 0.0 for a productive sprint where phase_timings.WINDUP is 0.0.
                    "findings_per_minute": round(
                        ((result.accepted_findings or 0) + (result.public_accepted_findings or 0))
                        / (actual_duration / 60.0),
                        2,
                    )
                    if actual_duration > 0
                    else 0.0,
                    # Sprint F202B: Identity stitching sidecar counters
                    "identity_candidates_found": result.identity_candidates_found,
                    "identity_findings_produced": result.identity_findings_produced,
                    # [F208J-B] Canonical acquisition terminality and report truth
                    **_acq_payload,
                },
                top_nodes=top_seed_nodes,
                phase_durations=_phase_durations,
                # Sprint F155: Canonical truth enrichment — additive, derived-only
                runtime_truth=runtime_truth,
                execution_context={
                    "query": query,
                    "requested_duration_s": duration_s,
                    "actual_duration_s": round(actual_duration, 2),
                    "source_count": len(live_feed_urls),
                    "sources": live_feed_urls,
                    "platform": {
                        "python_version": __import__("sys").version.split()[0],
                        "macos_version": __import__("platform").mac_ver()[0] or "unknown",
                    },
                    "report_path": str(report_path),
                    "git_snapshot": "unknown",
                    "export_dir": export_dir,
                },
                # Sprint F155: canonical_run_summary inline (already computed in report_dict)
                canonical_run_summary={
                    "meaningful": runtime_truth["is_meaningful"],
                    "primary_signal": runtime_truth["primary_signal_source"],
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
                    "runtime_truth_level": runtime_truth_level,
                    "checkpoint_zero_category": _ckpt_category,
                    "checkpoint_zero_reason": _checkpoint_zero_reason,
                    "observed_run_tuple": observed_run_tuple,
                    "canonical_sprint_owner": "core.__main__.run_sprint",
                    "canonical_path_used": "run_sprint",
                    "effective_source_mix": src_mix_str,
                    "effective_parallelism": len(live_feed_urls),
                    "effective_timeouts": {},
                    "active_iteration_count": active_iterations,
                    # F166B+F178B: Pre-loop and pre-active starvation surfaces
                    "pre_loop_elapsed_s": result.pre_loop_elapsed_s,
                    "pre_loop_blocker_reason": result.pre_loop_blocker_reason,
                    "pre_active_starvation": result.pre_active_starved,
                    "export_finish_layer_status": _export_finish_status,
                    # Sprint F163C: public_error must surface at canonical boundary
                    "public_error": result.public_error,
                    # Sprint F194A: CT log canonical findings — additive to sprint truth
                    "ct_log_discovered": result.ct_log_discovered,
                    "ct_log_stored": result.ct_log_stored,
                    "ct_log_accepted_findings": result.ct_log_accepted_findings,
                    # Sprint F160E: Canonical timing truth — separates active window from full run
                    "timing_truth": timing_truth,
                    # [F208J-B] Canonical acquisition terminality and report truth — same payload
                    # as what entered scorecard above, ensuring export_sprint() receives consistent
                    # acquisition truth via both the scorecard and canonical_run_summary seams.
                    **_acq_payload,
                },
                synthesis_outcome_payload=None,  # synthesis_runner not exposed on lifecycle/scheduler
                # Sprint F153: Top-level sprint verdict propagated to export
                sprint_verdict=intel.get("sprint_verdict"),
                # Sprint F204E: Analyst brief from sprint teardown
                analyst_brief=scheduler.get_analyst_brief(),
                # Sprint F238E Phase C: Runtime timer events for optional debug export
                timer_events=getattr(result, "timer_events", None),
            )

            # Sprint F155: Log enrichment level
            logger.info(f"[EXPORT] {'fully_enriched' if _handoff_enriched else 'degraded'} → sprint_id={sprint_id}")

            # Sprint F500I: Lazy import — export_sprint heavy, only needed at end of sprint
            from hledac.universal.export.sprint_exporter import export_sprint

            export_result = await export_sprint(store=store, handoff=handoff, sprint_id=sprint_id)
            logger.info(f"[EXPORT] finish layer → seeds={export_result.get('seeds_json', '')}")

            # Deep probe runs AFTER export completes — post-sprint, non-blocking
            if deep_probe_enabled:
                try:
                    from hledac.universal.deep_research.probe_runner import run_deep_probe_if_enabled

                    probe_result = await run_deep_probe_if_enabled(
                        query=query,
                        store=store,
                        deep_probe_enabled=True,
                    )
                    if probe_result:
                        logger.info(f"[DEEP_PROBE] completed: {probe_result}")
                except Exception as probe_err:
                    logger.warning(f"[DEEP_PROBE] probe runner failed (non-fatal): {probe_err}")
        except Exception as ex:
            logger.warning(f"[EXPORT] sprint_exporter seam failed (non-fatal): {ex}")

    finally:
        # F4.4: Graceful task cancellation via trio-style cancel_scope_drain.
        # Prevents "Task was destroyed but it is pending" warnings.
        # SIGTERM during finally = CancelledError propagates through aclose calls;
        # protected by cancel_scope_drain's 5s timeout.
        from hledac.universal.utils.async_helpers import cancel_scope_drain

        count = await cancel_scope_drain(timeout=5.0, label="orphan_drain")
        if count > 0:
            logger.debug("[SPRINT] Cancelled and drained %d orphan tasks", count)

        # Sprint F195C: Finalize dashboard display
        if _dashboard is not None:
            try:
                elapsed_s = time.monotonic() - _phase_times["BOOT"]
                _dashboard.finish(result, elapsed_s)
            except Exception as e:
                logger.warning(f"Dashboard finish failed: {e}")  # fail-safe
        # F285 LIFO cleanup: scheduler (Metal, LMDB, Hermes, transports) closes FIRST,
        # then store (DuckDB WAL drains). This prevents the race where scheduler
        # continues writing to DuckDB after close.
        # CancelledError propagates; all other exceptions are fail-safe logged.
        try:
            await scheduler.aclose(timeout_s=10.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[F285] scheduler.aclose() in finally block failed: {e}")  # fail-safe
        # F285-RESOURCE: Close EvidenceLog async resources (_flush_task, _db, _arrow_writer)
        if _elog is not None:
            try:
                await _elog.aclose()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"[F285] _elog.aclose() in finally block failed: {e}")  # fail-safe
        try:
            await store.aclose(timeout_s=10.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[ISSUE-2] store.aclose() in finally block failed: {e}")  # fail-safe
        # Sprint F206K: Close HTTPX client if it was lazily instantiated
        try:
            from hledac.universal.transport.httpx_client import close_httpx_client_async

            await close_httpx_client_async()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[TEARDOWN] HTTPX client close failed: {e}")  # fail-soft
        # Sprint F206L: Close curl_cffi sessions if they were lazily instantiated
        try:
            from hledac.universal.transport.curl_cffi_runtime import close_curl_cffi_sessions_async

            await close_curl_cffi_sessions_async()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[TEARDOWN] curl_cffi sessions close failed: {e}")  # fail-soft
        # Sprint F219K: Close public_fetcher local Tor/I2P sessions
        try:
            from hledac.universal.fetching.public_fetcher import close_public_fetcher_sessions_async

            await close_public_fetcher_sessions_async()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[TEARDOWN] public_fetcher sessions close failed: {e}")  # fail-soft
        # Sprint F216A: Close aiohttp session used by public_fetcher
        try:
            from hledac.universal.network.session_runtime import close_aiohttp_session_async

            await close_aiohttp_session_async()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[TEARDOWN] aiohttp session close failed: {e}")  # fail-soft

        # F266-U2/U3: Finalize memory hygiene hooks. The cycle maintain
        # call re-pins the surviving long-lived set into the permanent
        # generation (bounds gen-2 growth across many sprints). Pressure
        # relief stop is idempotent and bounded (5s timeout).
        try:
            await _memory_cycle.stop_pressure_relief_loop()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[TEARDOWN] pressure_relief stop failed: {e}")  # fail-soft
        # F266-U4 FIX: gc.collect() can block the event loop — offload to
        # a thread so teardown doesn't starve in-flight coroutines.
        try:
            await asyncio.to_thread(_memory_cycle.gc_cycle_maintain, force=False)
        except Exception as e:
            logger.debug(f"[TEARDOWN] gc_cycle_maintain failed: {e}")  # fail-soft
        # F266-LOCK: Release sprint-level lock — must happen after all cleanup
        # so that concurrent sprints don't steal the lock before teardown completes.
        if _sprint_lock_mgr is not None:
            try:
                _sprint_lock_mgr.release()
                logger.debug(f"[F266-LOCK] Released sprint lock: {_sprint_lock_path}")
            except Exception as e:
                logger.debug(f"[F266-LOCK] Lock release failed (non-fatal): {e}")  # fail-safe


# =============================================================================
# CLI entry point
# =============================================================================


async def run_ct_pivot(domain: str) -> None:
    """Run CT log pivot for a single domain."""
    # Sprint F500I: Lazy import — CTLogClient and TorTransport only needed for CT pivot
    from hledac.universal.intelligence.ct_log_client import CTLogClient
    from hledac.universal.transport.tor_transport import TorTransport

    ct_client = CTLogClient(TOR_ROOT.parent / "cache" / "crt")
    tor_transport = TorTransport()

    tor_started = await tor_transport.start()
    if tor_started:
        logger.info("Tor ready for .onion fetches")
    else:
        logger.warning("Tor unavailable — .onion sources disabled")

    try:
        async with httpx.AsyncClient() as sess:
            result = await ct_client.pivot_domain(domain, sess)
        print(f"\nCT LOG PIVOT: {result['domain']}")
        print(f"  Cert count:  {result['cert_count']}")
        print(f"  First cert: {result['first_cert']}")
        print(f"  Last cert:  {result['last_cert']}")
        print(f"  SAN domains: {len(result['san_names'])}")
        for san in result["san_names"][:10]:
            print(f"    {san}")
        if result["san_names"] and len(result["san_names"]) > 10:
            print(f"    ... (+{len(result['san_names']) - 10} more)")
        print(f"  Issuers: {result['issuers']}")
    finally:
        await tor_transport.stop()
        logger.info("CT pivot done, Tor stopped")


async def run_semantic_pivot(query: str, top_k: int = 10) -> None:
    """
    Sprint 8SB: Semantic pivot — ANN search for similar findings.

    Loads SemanticStore, runs semantic_pivot, prints results.
    """
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
                import datetime

                print(f"               ts: {datetime.datetime.fromtimestamp(ts):.0f}")  # noqa: DTZ006
        print(f"\nTotal results: {len(results)}")
    finally:
        await store.close()


def _install_signal_handler_for_loop(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
) -> Callable[[], None]:
    """
    Install SIGINT/SIGTERM handlers bound to a specific loop and event.

    Returns a cleanup function that restores previous signal handlers.
    Handler is idempotent, fail-soft, never calls loop.stop().
    """
    _prev_int: Callable[[int, Any], Any] | None = None
    _prev_term: Callable[[int, Any], Any] | None = None

    def _handler(signum: int, frame: Any) -> None:
        sig_name = (
            getattr(signal.Signals, "SIGINT", None) and signal.Signals(signum).name
            if hasattr(signal, "Signals")
            else str(signum)
        )  # noqa: E501
        logging.info(f"[SIGNAL] Received {sig_name} — cooperative shutdown")
        try:
            if loop.is_running() and not loop.is_closed():
                loop.call_soon_threadsafe(shutdown_event.set)
            else:
                # Loop not running — set event directly
                shutdown_event.set()
        except Exception:  # noqa: BLE001
            pass

    try:
        _prev_int = signal.signal(signal.SIGINT, _handler)
        _prev_term = signal.signal(signal.SIGTERM, _handler)
        logging.info("[SIGNAL] SIGINT/SIGTERM handlers installed")
    except (ImportError, AttributeError, OSError, TypeError) as e:
        logging.warning(f"[SIGNAL] Signal handlers not available: {e}")

    def _restore() -> None:
        try:
            if _prev_int is not None:
                signal.signal(signal.SIGINT, _prev_int)
            if _prev_term is not None:
                signal.signal(signal.SIGTERM, _prev_term)
        except Exception:  # noqa: BLE001
            pass

    return _restore


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
    Extracted CLI sprint wiring (Issue #7).
    Owns: loop + signals + shutdown_event + task lifecycle.
    Canonical state lives in run_sprint(), not here.
    """
    import contextlib

    sprint_flags = SprintFlags(
        force=args.force,
        no_communication=getattr(args, "no_communication", False),
        no_stealth=getattr(args, "no_stealth", False),
        no_ghost=getattr(args, "no_ghost", False),
        no_coordination=getattr(args, "no_coordination", False),
        production=getattr(args, "production", False),
        hermes_force=getattr(args, "force_hermes", False),
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    shutdown_event = asyncio.Event()
    restore_signals = _install_signal_handler_for_loop(loop, shutdown_event)
    done: set = set()
    pending: set = set()
    try:
        sprint_task = loop.create_task(
            run_sprint(
                args.query,
                float(args.duration),
                args.export_dir,
                args.aggressive,
                args.deep_probe,
                deep_research=args.deep_research,
                extreme_mode=args.extreme,
                acquisition_profile=args.acquisition_profile,
                rl_train_mode=args.rl_train,
                flags=sprint_flags,
            )
        )
        sig_task = loop.create_task(shutdown_event.wait())
        done, pending = loop.run_until_complete(
            asyncio.wait([sprint_task, sig_task], return_when=asyncio.FIRST_COMPLETED)
        )
        exc = sprint_task.exception()
        if exc is not None:
            raise exc
        if sprint_task not in done:
            sprint_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                loop.run_until_complete(sprint_task)
    except (NameError, TypeError) as _prog_err:
        logger.exception(
            "[MAIN] Fatal programmer error in core/__main__.py --sprint: %s",
            _prog_err,
        )
        sys.exit(1)
    except SystemExit:
        raise
    finally:
        restore_signals()
        for task in pending:
            task.cancel()
        loop.close()


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
    args = args_with_rl_resolution = parser.parse_args()
    # F261QMIX: --rl-no-train overrides --rl-train (explicit disable wins)
    if args.rl_no_train:
        args.rl_train = False
    # F261QMIX: env-var override for train interval
    if args.rl_train_interval is not None:
        import os as _os

        _os.environ["HLEDAC_RL_TRAIN_INTERVAL"] = str(args.rl_train_interval)
    args = args_with_rl_resolution

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

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
        asyncio.run(run_ct_pivot(args.ct_pivot))
    elif args.sprint:
        _run_sprint_loop(args)
    elif args.pivot:
        asyncio.run(run_semantic_pivot(args.pivot, top_k=args.pivot_k))
    else:
        print("Hledac Sprint 8RA Runner")
        print("  python -m hledac.universal.runtime.sprint_entrypoint --sprint --query '...' --duration 1800")
        print("  python -m hledac.universal.core --ct-pivot example.com")
        print("  python -m hledac.universal.core --pivot 'ransomware CVE' --pivot-k 10")


if __name__ == "__main__":
    main()
