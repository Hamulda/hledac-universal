"""STEP 1 — SprintSchedulerResult + SprintResultBuilder.

Extracted from sprint_scheduler.py (33 449 LOC → modular package).
F350M-R / Issue #P2.

~380 fields — frozen dataclass with SoA int-counter layout.
"""


from dataclasses import dataclass, field
from typing import Any


# ── SprintSchedulerResult ─────────────────────────────────────────────────────

# Sentinel for "unset" on optional fields
_UNSET: Any = object()


@dataclass
class SprintSchedulerResult:
    """Outcome of one sprint run.

    STEP 1 extracted from sprint_scheduler.py (33 449 LOC → modular package).
    F350M-R / Issue #P2.
    """

    # ── Core cycle counters ──────────────────────────────────────────────────
    cycles_started: int = 0
    cycles_completed: int = 0
    consecutive_empty_cycles: int = 0
    max_consecutive_empty_cycles: int = 0
    unique_entry_hashes_seen: int = 0
    duplicate_entry_hashes_skipped: int = 0
    total_pattern_hits: int = 0

    # ── Signal funnel ────────────────────────────────────────────────────────
    entries_seen: int = 0
    entries_scanned: int = 0
    entries_with_hits: int = 0
    findings_built_pre_store: int = 0
    signal_stage: str = "unknown"
    accepted_findings: int = 0

    # ── Per-source ───────────────────────────────────────────────────────────
    entries_per_source: dict[str, int] = field(default_factory=dict)
    hits_per_source: dict[str, int] = field(default_factory=dict)

    # ── Lifecycle ────────────────────────────────────────────────────────────
    final_phase: str = "BOOT"
    export_paths: list[str] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""
    stop_requested: bool = False

    # ── Synthesis ────────────────────────────────────────────────────────────
    synthesis_success: bool = False
    synthesis_engine: str = "unknown"
    synthesis_findings_count: int = 0
    ioc_cooccurrence_edges: int = 0
    synthesis_text: str = ""
    hypotheses_generated: int = 0

    # ── Public pipeline ──────────────────────────────────────────────────────
    pii_findings_anonymized: int = 0
    public_discovered: int = 0
    public_fetched: int = 0
    public_matched_patterns: int = 0
    public_accepted_findings: int = 0
    public_stored_findings: int = 0
    public_error: str = ""
    public_provider_selection_debug: dict = field(default_factory=dict)
    public_backend_degraded: bool = False
    dominant_public_blocker: str = ""
    public_terminal_stage: str = ""
    public_stage_counters: dict = field(default_factory=dict)
    public_discovery_empty_reason: str = ""
    public_branch_timed_out: bool = False

    # ── CT log pipeline ──────────────────────────────────────────────────────
    ct_log_discovered: int = 0
    ct_log_stored: int = 0
    ct_log_accepted_findings: int = 0
    ct_log_error: str = ""

    # ── CT bridge loss audit ─────────────────────────────────────────────────
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
    ct_storage_attempted: bool = False
    ct_storage_accepted: bool = False
    ct_terminal_stage: str = ""
    ct_prelude_missing_but_final_attempted: bool = False
    ct_branch_timed_out: bool = False

    # ── Pre-loop / starvation ────────────────────────────────────────────────
    entered_active_at_monotonic: float | None = None
    pre_loop_elapsed_s: float | None = None
    first_cycle_started_at_monotonic: float | None = None
    pre_active_starved: bool = False
    pre_loop_blocker_reason: str = ""

    # ── Dedup preload ───────────────────────────────────────────────────────
    dedup_preload_count: int | None = None
    dedup_preload_elapsed_s: float | None = None

    # ── Feed blockers ────────────────────────────────────────────────────────
    feed_zero_yield_detected: bool = False
    feed_inaccessible_detected: bool = False
    feed_content_empty_detected: bool = False
    feed_no_pattern_with_content: bool = False
    findings_build_loss_detected: bool = False
    feed_no_signal_sources: list[str] = field(default_factory=list)
    dominant_feed_blocker: str = ""
    dominant_branch_blocker: str = ""
    branch_degradation_summary: str = ""

    # ── RL / policy quality ─────────────────────────────────────────────────
    policy_quality_feedback_calls: int = 0
    policy_quality_feedback_decisions: int = 0
    policy_quality_feedback_sources: int = 0
    policy_quality_feedback_errors: int = 0
    rl_enabled: bool = False
    rl_epsilon: float = 0.0
    rl_total_reward: float = 0.0
    rl_last_action: int = 0
    rl_lane_combo: frozenset = field(default_factory=frozenset)
    rl_suggested_pivot: str = ""

    # ── Hermes / MLX ────────────────────────────────────────────────────────
    hermes_model_loaded: bool = False
    hermes_load_attempted: bool = False
    hermes_load_reason: str = ""
    hermes_load_elapsed_s: float = 0.0
    mlx_batcher_stats: dict = field(default_factory=dict)

    # ── Drain / pressure ────────────────────────────────────────────────────
    pattern_extraction_drain_completed: int = 0
    pattern_extraction_drain_timed_out: int = 0
    pattern_extraction_drain_elapsed_s: float = 0.0
    malloc_pressure_relief_count: int = 0
    malloc_pressure_relief_last_rc: int = 0
    malloc_pressure_relief_last_at_s: float = 0.0
    captcha_hits: int = 0
    circuit_breaker_opens: int = 0

    # ── Branch / aggressive ─────────────────────────────────────────────────
    branch_timeout_count: int = 0
    branch_skipped_remaining_too_low: int = 0
    dynamic_branch_floor_s: float = 0.0
    effective_windup_lead_used_s: float = 0.0
    windup_lead_adaptive_factor: float = 1.0

    # ── DuckDB / storage ────────────────────────────────────────────────────
    duckdb_mode: str = "unknown"
    arrow_batch_hard_cap: int = 0
    arrow_batch_dropped_after_flush_failure: int = 0
    arrow_last_flush_error: str = ""
    arrow_metrics: dict = field(default_factory=dict)

    # ── Forensics / multimodal ───────────────────────────────────────────────
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

    # ── Sidecars ────────────────────────────────────────────────────────────
    sidecars_skipped: tuple[str, ...] = ()
    acquisition_lanes_skipped: int = 0
    cc_archive_injected: int = 0
    academic_findings_count: int = 0
    dht_findings_produced: int = 0

    # ── RDAP ────────────────────────────────────────────────────────────────
    rdap_enrichment_attempted: int = 0
    rdap_enrichment_findings_built: int = 0
    rdap_enrichment_findings_stored: int = 0
    rdap_enrichment_rejections: int = 0
    rdap_enrichment_error: str | None = None

    # ── Security ────────────────────────────────────────────────────────────
    security_rejected_count: int = 0
    pii_redacted_count: int = 0

    # ── Memory / hardware ───────────────────────────────────────────────────
    peak_rss_gib: float = 0.0
    budget_violations: int = 0
    governor_uma_state: str = ""
    governor_system_used_gib: float = 0.0
    governor_swap_detected: bool = False
    governor_io_only: bool = False
    pressure_violations: int = 0

    # ── IPFS ────────────────────────────────────────────────────────────────
    lane_ipfs_accepted_findings: int = 0
    ipfs_cids_attempted: int = 0
    ipfs_findings_accepted: int = 0

    # ── Acquisition lane outcomes ────────────────────────────────────────────
    acquisition_lane_outcomes: tuple = ()
    lane_ct_accepted_findings: int = 0
    lane_wayback_accepted_findings: int = 0
    lane_pdns_accepted_findings: int = 0
    lane_blockchain_accepted_findings: int = 0
    lane_public_accepted_findings: int = 0
    lane_doh_accepted_findings: int = 0

    # ── DOH ─────────────────────────────────────────────────────────────────
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

    # ── Wayback ─────────────────────────────────────────────────────────────
    wayback_attempted: bool = False
    wayback_raw_count: int = 0
    wayback_candidates_built: int = 0
    wayback_accepted_count: int = 0
    wayback_advisory_clues_count: int = 0
    wayback_changed_url_count: int = 0
    wayback_added_url_count: int = 0
    wayback_digest_changed_count: int = 0
    wayback_unchanged_rejected: int = 0

    # ── Passive DNS ─────────────────────────────────────────────────────────
    passive_dns_attempted: bool = False
    passive_dns_raw_count: int = 0
    passive_dns_candidates_built: int = 0
    passive_dns_accepted_count: int = 0
    passive_dns_advisory_clues_count: int = 0
    passive_dns_private_ip_rejected: int = 0
    passive_dns_empty_ip_rejected: int = 0

    # ── Nonfeed / predispatch ───────────────────────────────────────────────
    nonfeed_predispatch_attempted: bool = False
    nonfeed_predispatch_skipped: dict[str, str] = field(default_factory=dict)
    nonfeed_predispatch_lanes: tuple[str, ...] = ()
    nonfeed_predispatch_duration_s: float = 0.0
    windup_blocked_until_nonfeed_attempted: bool = False
    nonfeed_plan_debug: Any = None
    nonfeed_predispatch_checked: bool = False
    nonfeed_predispatch_ran: bool = False
    nonfeed_predispatch_reason: str | None = None
    nonfeed_predispatch_outcomes_count: int = 0
    nonfeed_budget_active: bool = False
    nonfeed_budget_expected_lanes: tuple[str, ...] = ()
    nonfeed_budget_terminal_lanes: tuple[str, ...] = ()
    nonfeed_budget_unresolved_lanes: tuple[str, ...] = ()
    feed_suppressed_by_nonfeed_budget: int = 0
    feed_suppression_count: int = 0
    feed_suppression_reason: str = ""

    # ── Nonfeed prelude ─────────────────────────────────────────────────────
    nonfeed_prelude_enabled: bool = False
    nonfeed_prelude_expected_lanes: tuple[str, ...] = ()
    nonfeed_prelude_attempted_lanes: tuple[str, ...] = ()
    nonfeed_prelude_terminal_lanes: tuple[str, ...] = ()
    nonfeed_prelude_missing_lanes: tuple[str, ...] = ()
    nonfeed_prelude_accepted_by_lane: dict[str, int] = field(default_factory=dict)
    nonfeed_prelude_error_by_lane: dict[str, str] = field(default_factory=dict)
    nonfeed_prelude_duration_s: float = 0.0
    nonfeed_prelude_feed_blocked_until_complete: bool = False

    # ── Nonfeed sticky lanes ────────────────────────────────────────────────
    nonfeed_priority_enabled: bool = False
    nonfeed_profile_expected_lanes: tuple[str, ...] = ()
    nonfeed_expected_lanes: tuple[str, ...] = ()
    nonfeed_expected_lanes_source: str = ""

    # ── Seed context ────────────────────────────────────────────────────────
    seed_context_available: bool = False
    seed_context_propagated: bool = False
    lanes_unlocked_by_seed_context: list[str] = field(default_factory=list)
    seed_context_skip_reason: str = ""
    seed_context_source: str = ""

    # ── Feed domain seeds ───────────────────────────────────────────────────
    feed_domain_seeds: tuple[str, ...] = ()

    # ── Pivot / planner ─────────────────────────────────────────────────────
    pivot_seed_count: int = 0
    pivot_seed_type_counts: dict[str, int] = field(default_factory=dict)
    pivot_seed_sample: tuple[str, ...] = ()
    pivot_seed_domains: tuple[str, ...] = ()
    pivot_seed_ips: tuple[str, ...] = ()
    pivot_seed_urls: tuple[str, ...] = ()
    pivot_seed_hashes: tuple[str, ...] = ()
    pivot_seed_cves: tuple[str, ...] = ()

    # ── Next seeds ─────────────────────────────────────────────────────────
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

    # ── Quantum / graph ────────────────────────────────────────────────────
    quantum_path_seeds: list[str] = field(default_factory=list)
    graph_rag_context_count: int = 0

    # ── Dark surface / Gopher / BGP / Banner ────────────────────────────────
    dark_surface_pivots_attempted: int = 0
    dark_surface_pivots_accepted: int = 0
    gopher_findings_ingested: int = 0
    bgp_enrichment_findings_ingested: int = 0
    banner_grab_findings_ingested: int = 0

    # ── Transport efficiency ─────────────────────────────────────────────────
    transport_efficiency: dict[str, int] = field(default_factory=dict)

    # ── RL / findings ──────────────────────────────────────────────────────
    findings_deduplicated: int = 0
    hypothesis_contradictions_detected: int = 0
    cover_traffic_fired: int = 0
    rl_suggested_pivot: str = ""

    # ── Pre-windup barrier ─────────────────────────────────────────────────
    prewindup_barrier_checked: bool = False
    prewindup_barrier_required_lanes: tuple[str, ...] = ()
    prewindup_barrier_satisfied: bool = False
    prewindup_barrier_attempted_lanes: tuple[str, ...] = ()
    prewindup_barrier_skipped_lanes: dict[str, str] = field(default_factory=dict)
    prewindup_barrier_errors: dict[str, str] = field(default_factory=dict)
    prewindup_barrier_duration_s: float = 0.0
    windup_delayed_for_nonfeed: bool = False
    prewindup_barrier_delayed_cycle: bool = False
    prewindup_guard_async_bridge_used: bool = False
    prewindup_guard_async_error: str = ""
    prewindup_guard_fail_closed: bool = False

    # ── Windup guard ────────────────────────────────────────────────────────
    windup_guard_call_count: int = 0
    windup_guard_callback_supplied_count: int = 0
    windup_guard_callback_executed_count: int = 0
    windup_guard_last_reason: str = ""
    windup_guard_last_phase: str = ""
    windup_guard_last_allowed: bool | None = None
    windup_guard_last_callback_not_executed_reason: str = ""

    # ── Return guard ────────────────────────────────────────────────────────
    return_guard_checked: bool = False
    return_guard_required_lanes: tuple[str, ...] = ()
    return_guard_satisfied: bool = False
    return_guard_delayed_for_nonfeed: bool = False
    return_guard_block_reason: str = ""
    return_guard_attempted_lanes: tuple[str, ...] = ()
    return_guard_skipped_lanes: dict[str, str] = field(default_factory=dict)
    return_guard_errors: dict[str, str] = field(default_factory=dict)

    # ── Early exit / timing ─────────────────────────────────────────────────
    requested_duration_s: float = 0.0
    actual_duration_s: float = 0.0
    elapsed_pct: float = 0.0
    active_window_budget_s: float = 0.0
    active_window_elapsed_s: float = 0.0
    windup_efficiency: float = 0.0
    early_exit_class: str = ""
    early_exit_reason: str = ""
    run_error_class: str = ""
    run_error: str = ""

    # ── Feed dominance ──────────────────────────────────────────────────────
    feed_dominance_ratio: float = 0.0
    feed_dominance_class: str = ""
    feed_dominance_guard_triggered: bool = False
    should_recommend_nonfeed_diagnostic: bool = False

    # ── Feed budget ─────────────────────────────────────────────────────────
    feed_budget_active: bool = False
    feed_budget_reason: str = ""
    feed_accepted_before_cap: int = 0
    feed_suppressed_by_budget: int = 0
    feed_budget_per_source: dict[str, int] = field(default_factory=dict)
    top_feed_source_counts: tuple[tuple[str, int], ...] = ()
    max_per_source_applied: str = ""

    # ── Acquisition prelude ────────────────────────────────────────────────
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

    # ── Acquisition plan ───────────────────────────────────────────────────
    acquisition_plan_build_failed: bool = False
    acquisition_plan_build_error_type: str = ""
    acquisition_plan_build_error: str = ""
    acquisition_terminality_checked: bool = False
    acquisition_terminality_satisfied: bool = False
    acquisition_terminality_missing_lanes: tuple[str, ...] = ()
    acquisition_terminality_report: dict = field(default_factory=dict)

    # ── Scheduler exit tracer ─────────────────────────────────────────────
    scheduler_exit_path: str | None = None
    scheduler_exit_reason: str | None = None
    scheduler_exit_phase: str | None = None
    scheduler_exit_cycle: int | None = None
    scheduler_exit_elapsed_s: float | None = None
    scheduler_exit_guard_checked: bool = False
    scheduler_exit_guard_required: tuple[str, ...] = ()
    scheduler_exit_guard_satisfied: bool | None = None

    # ── Nonfeed mission ─────────────────────────────────────────────────────
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

    # ── Pivot lane ─────────────────────────────────────────────────────────
    pivot_lane_plan_count: int = 0
    planned_pivot_lanes: tuple[str, ...] = ()

    # ── Seed quality ────────────────────────────────────────────────────────
    seed_quality_checked: bool = False
    seed_quality_keep_count: int = 0
    seed_quality_drop_count: int = 0
    seed_quality_drop_reasons: dict = field(default_factory=dict)
    seed_quality_kept_sample: list = field(default_factory=list)
    seed_quality_dropped_sample: list = field(default_factory=list)
    seed_quality_bypass_reason: str = ""

    # ── Source family events ─────────────────────────────────────────────────
    source_family_events: list[dict] = field(default_factory=list)
    MAX_SOURCE_FAMILY_EVENTS: int = 200

    # ── Acquisition plan for prelude ───────────────────────────────────────
    acquisition_plan_present_for_prelude: bool = False
    acquisition_plan_lanes_for_prelude: tuple[str, ...] = ()
    acquisition_plan_enabled_lanes_for_prelude: tuple[str, ...] = ()
    acquisition_plan_profile_for_prelude: str = ""
    acquisition_plan_build_error_for_prelude: str = ""

    # ── Research context ────────────────────────────────────────────────────
    research_context: Any = None

    # ── Timer events ────────────────────────────────────────────────────────
    timer_events: list[dict] | None = None

    # ── Sprint F214Q ────────────────────────────────────────────────────────
    run_error_class: str = ""
    run_error: str = ""

    # ── SoA counter layout (lazy allocation in __post_init__) ─────────────────
    _int_counter_layout: Any = None

    def __post_init__(self) -> None:
        """Sprint P0-1: lazily allocate the SoA counter layout.

        L.1  Allocated exactly once per instance.
        L.2  Fail-soft: leaves layout as None on any error.
        L.3  Idempotent — safe to call multiple times.
        """
        if self._int_counter_layout is not None:
            return
        # Runtime import from original module (avoids duplicating INT_COUNTER_LAYOUT_NAMES)
        try:
            import runtime.sprint_scheduler as _ss
            _layout_class = getattr(_ss, 'IntCounterLayoutRust', None) or getattr(_ss, 'IntCounterLayout', None)
            _names = getattr(_ss, 'INT_COUNTER_LAYOUT_NAMES', ())
            if _layout_class is not None and _names:
                object.__setattr__(self, "_int_counter_layout", _layout_class(_names))
            else:
                object.__setattr__(self, "_int_counter_layout", None)
        except Exception:
            object.__setattr__(self, "_int_counter_layout", None)

    def cycles_started_(self) -> int:
        return self.cycles_started

    def cycles_completed_(self) -> int:
        return self.cycles_completed

    def bump_counter(self, name: str, n: int = 1) -> int:
        layout = self._int_counter_layout
        if layout is not None:
            return layout.bump(name, n)
        return 0


# ── SprintResultBuilder ────────────────────────────────────────────────────────

@dataclass
class SprintResultBuilder:
    """Fluent builder for SprintSchedulerResult (F350M-R / Issue #6).

    Usage::

        result = (SprintResultBuilder()
                  .with_cycles_started(1)
                  .with_accepted_findings(42)
                  .build())
    """

    _result: SprintSchedulerResult = field(default_factory=SprintSchedulerResult)

    def build(self) -> SprintSchedulerResult:
        return self._result

    def _field_names(cls) -> list[str]:
        return list(SprintSchedulerResult.__dataclass_fields__.keys())

    def _set(self, name: str, value: object) -> "SprintResultBuilder":
        object.__setattr__(self._result, name, value)
        return self

    def __getattr__(self, name: str) -> object:
        return getattr(self._result, name)

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_result":
            object.__setattr__(self, name, value)
        else:
            object.__setattr__(self._result, name, value)

    # ── Fluent with_ ───────────────────────────────────────────────────────

    def with_cycles_started(self, v: int) -> "SprintResultBuilder":
        self._result.cycles_started = v; return self

    def with_cycles_completed(self, v: int) -> "SprintResultBuilder":
        self._result.cycles_completed = v; return self

    def with_aborted(self, v: bool) -> "SprintResultBuilder":
        self._result.aborted = v; return self

    def with_abort_reason(self, v: str) -> "SprintResultBuilder":
        self._result.abort_reason = v; return self

    def with_final_phase(self, v: str) -> "SprintResultBuilder":
        self._result.final_phase = v; return self

    def with_accepted_findings(self, v: int) -> "SprintResultBuilder":
        self._result.accepted_findings = v; return self

    def with_total_pattern_hits(self, v: int) -> "SprintResultBuilder":
        self._result.total_pattern_hits = v; return self

    def with_unique_entry_hashes_seen(self, v: int) -> "SprintResultBuilder":
        self._result.unique_entry_hashes_seen = v; return self

    def with_duplicate_entry_hashes_skipped(self, v: int) -> "SprintResultBuilder":
        self._result.duplicate_entry_hashes_skipped = v; return self

    def with_consecutive_empty_cycles(self, v: int) -> "SprintResultBuilder":
        self._result.consecutive_empty_cycles = v; return self

    def with_max_consecutive_empty_cycles(self, v: int) -> "SprintResultBuilder":
        self._result.max_consecutive_empty_cycles = v; return self

    def with_entries_per_source(self, v: dict[str, int]) -> "SprintResultBuilder":
        self._result.entries_per_source = v; return self

    def with_hits_per_source(self, v: dict[str, int]) -> "SprintResultBuilder":
        self._result.hits_per_source = v; return self

    def with_export_paths(self, v: list[str]) -> "SprintResultBuilder":
        self._result.export_paths = v; return self

    def with_stop_requested(self, v: bool) -> "SprintResultBuilder":
        self._result.stop_requested = v; return self

    def with_synthesis_success(self, v: bool) -> "SprintResultBuilder":
        self._result.synthesis_success = v; return self

    def with_synthesis_engine(self, v: str) -> "SprintResultBuilder":
        self._result.synthesis_engine = v; return self

    def with_synthesis_findings_count(self, v: int) -> "SprintResultBuilder":
        self._result.synthesis_findings_count = v; return self

    def with_synthesis_text(self, v: str) -> "SprintResultBuilder":
        self._result.synthesis_text = v; return self

    def with_hypotheses_generated(self, v: int) -> "SprintResultBuilder":
        self._result.hypotheses_generated = v; return self

    def with_public_discovered(self, v: int) -> "SprintResultBuilder":
        self._result.public_discovered = v; return self

    def with_public_fetched(self, v: int) -> "SprintResultBuilder":
        self._result.public_fetched = v; return self

    def with_public_matched_patterns(self, v: int) -> "SprintResultBuilder":
        self._result.public_matched_patterns = v; return self

    def with_public_accepted_findings(self, v: int) -> "SprintResultBuilder":
        self._result.public_accepted_findings = v; return self

    def with_public_stored_findings(self, v: int) -> "SprintResultBuilder":
        self._result.public_stored_findings = v; return self

    def with_public_error(self, v: str) -> "SprintResultBuilder":
        self._result.public_error = v; return self

    def with_ct_log_discovered(self, v: int) -> "SprintResultBuilder":
        self._result.ct_log_discovered = v; return self

    def with_ct_log_stored(self, v: int) -> "SprintResultBuilder":
        self._result.ct_log_stored = v; return self

    def with_ct_log_accepted_findings(self, v: int) -> "SprintResultBuilder":
        self._result.ct_log_accepted_findings = v; return self

    def with_ct_log_error(self, v: str) -> "SprintResultBuilder":
        self._result.ct_log_error = v; return self

    def with_entered_active_at_monotonic(self, v: float) -> "SprintResultBuilder":
        self._result.entered_active_at_monotonic = v; return self

    def with_pre_loop_elapsed_s(self, v: float) -> "SprintResultBuilder":
        self._result.pre_loop_elapsed_s = v; return self

    def with_first_cycle_started_at_monotonic(self, v: float) -> "SprintResultBuilder":
        self._result.first_cycle_started_at_monotonic = v; return self

    def with_pre_active_starved(self, v: bool) -> "SprintResultBuilder":
        self._result.pre_active_starved = v; return self

    def with_(self, field: str, value: object) -> "SprintResultBuilder":
        object.__setattr__(self._result, field, value)
        return self

    def update(self, **kwargs: object) -> "SprintResultBuilder":
        for k, v in kwargs.items():
            object.__setattr__(self._result, k, v)
        return self
