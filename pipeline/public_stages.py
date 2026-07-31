"""Public pipeline stages — PipelinePageResult, PipelineRunResult dataclasses and async_run_live_public_pipeline() entry point.

Extracted from live_public_pipeline.py.
Contains only: struct definitions + thin orchestration stub.

All heavy logic delegated to sibling modules:
- public_discovery: URL generation + _DiscoveryEngine
- public_patterns: pattern matching + quality scoring
- public_acceptance: CanonicalFinding construction
- public_fetch: page fetching + extraction
"""
from __future__ import annotations


import msgspec
from typing import Any

# ----------------------------------------------------------------------
# Pipeline result structs
# ----------------------------------------------------------------------


class PipelinePageResult(msgspec.Struct, frozen=True, gc=False):
    """Result of processing a single discovered page."""

    url: str
    fetched: bool
    matched_patterns: int
    accepted_findings: int
    stored_findings: int
    error: str | None = None
    quality_reason: str | None = None
    discovery_score: float | None = None
    discovery_reason: str | None = None
    discovery_signal: bool = False
    usable_signal: bool = False
    value_tier: str = "none"
    resolution_reason: str = ""
    discovery_false_positive: bool = False
    waste_category: str = ""
    structural_quality: str = ""
    failure_stage: str | None = None
    redirected: bool = False
    redirect_target: str | None = None
    js_renderer_skipped_reason: str | None = None
    fetch_blocked_reason: str | None = None
    rejection_reason: str | None = None
    terminal_reason: str | None = None
    public_surface_dup: bool = False
    build_attempted: bool = False


class PipelineRunResult(msgspec.Struct, frozen=True, gc=False):
    """Top-level result of a full pipeline run."""

    query: str
    discovered: int
    fetched: int
    matched_patterns: int
    accepted_findings: int
    stored_findings: int
    patterns_configured: int
    pages: tuple[PipelinePageResult, ...]
    error: str | None = None
    strong_pages: int = 0
    weak_pages_skipped: int = 0
    low_value_fetches: int = 0
    discovery_strong_content_weak: int = 0
    discovery_and_content_strong: int = 0
    discovery_squandered: int = 0
    noise_fetch_ratio: float = 0.0
    corroboration_vs_burn: float = 0.0
    public_next_action: str = ""
    public_confidence_note: str = ""
    public_branch_verdict: dict = {}
    usable_findings_ratio: float = 0.0
    discovery_to_findings_efficiency: float = 0.0
    quality_mix: str = ""
    public_proof_grade: str = ""
    public_value_density: float = 0.0
    top_waste_pattern: str = ""
    discovery_false_positive_count: int = 0
    waste_category_counts: dict = {}
    structural_health_ratio: float = 0.0
    factual_value_density: float = 0.0
    run_waste_pattern_code: str = ""
    waste_reason_breakdown: str = ""
    backend_degraded: bool = False
    public_discovery_blocker: str | None = None
    public_fetch_accessibility_blocker: bool = False
    public_discovery_fallback_state: str | None = None
    dominant_public_failure_mode: str | None = None
    public_stage_failure: str | None = None
    public_stage_failure_reason: str | None = None
    public_discovery_attempted: bool = False
    public_discovery_raw_count: int = 0
    public_discovery_deduped_count: int = 0
    public_pages_fetched: int = 0
    public_pages_accepted: int = 0
    public_pages_rejected: int = 0
    public_findings_accepted: int = 0
    zero_hit_accessible_fetch_count: int = 0
    ct_subdomain_injected: int = 0
    cc_archive_injected: int = 0
    academic_findings_count: int = 0
    pastebin_findings_count: int = 0
    github_secrets_count: int = 0
    public_bootstrap_enabled: bool = False
    public_bootstrap_candidates_count: int = 0
    public_bootstrap_fetch_attempted: int = 0
    public_bootstrap_fetch_success: int = 0
    public_bootstrap_accepted_findings: int = 0
    public_bootstrap_errors: int = 0
    public_bootstrap_order: str = "disabled"
    public_bootstrap_prevented_discovery_timeout: bool = False
    public_bootstrap_first_fetch_attempted: bool = False
    public_rescue_candidates_count: int = 0
    public_rescue_fetch_attempted: int = 0
    public_rescue_fetch_success: int = 0
    public_rescue_accepted_findings: int = 0
    public_rescue_errors: int = 0
    public_rescue_order: str = "disabled"
    keyword_seed_fallback_triggered: bool = False
    zero_hit_quality_reason_counts: dict = {}
    zero_hit_title_samples: tuple = ()
    public_zero_hit_summary: dict = {}
    public_discovered: int = 0
    public_fetch_attempted: int = 0
    public_fetch_skipped: int = 0
    public_fetch_skip_reason: str | None = None
    public_js_renderer_unavailable: int = 0
    public_xml_or_rss_detected: int = 0
    public_fetch_timeout_count: int = 0
    public_fetch_blocked_by_memory: int = 0
    public_discovery_cache_hit: int = 0
    public_discovery_query_count: int = 0
    public_fetch_candidate_count: int = 0
    public_fetch_gate: str = "none"
    public_fetch_attempted_urls_sample: tuple[str, ...] = ()
    public_acceptance_attempted: int = 0
    public_acceptance_accepted: int = 0
    public_acceptance_rejected: int = 0
    public_acceptance_reject_reasons: dict = {}
    public_accepted_url_sample: tuple[str, ...] = ()
    public_rejected_url_sample: tuple[str, ...] = ()
    public_terminal_classified_count: int = 0
    public_unclassified_count: int = 0
    public_terminal_reason_counts: dict = {}
    public_fetch_success: int = 0
    public_fetch_failed: int = 0
    public_skipped_duplicate: int = 0
    public_skipped_unsupported_scheme: int = 0
    public_skipped_memory_gate: int = 0
    public_skipped_quality_gate: int = 0
    public_skipped_browser_unavailable: int = 0
    public_skipped_xml_or_feed: int = 0
    public_skipped_timeout: int = 0
    public_skipped_fetch_error: int = 0
    public_rejected_no_pattern_match: int = 0
    public_rejected_low_information: int = 0
    public_rejected_duplicate: int = 0
    public_rejected_storage_rejected: int = 0
    public_build_success_count: int = 0
    public_build_failure_count: int = 0
    public_duplicate_count: int = 0
    public_acceptance_ratio: float = 0.0
    public_skipped_url_sample: tuple[str, ...] = ()
    public_rejected_url_samples: tuple[str, ...] = ()
    public_candidates_discovered: int = 0
    public_candidates_fetch_attempted: int = 0
    public_candidates_fetch_success: int = 0
    public_candidates_parse_success: int = 0
    public_candidates_pattern_matched: int = 0
    public_candidates_built: int = 0
    public_candidates_store_attempted: int = 0
    public_candidates_stored: int = 0
    public_candidates_rejected: int = 0
    public_rejection_summary: dict = {}
    public_terminal_stage: str = ""
    public_provider_selected: list[str] = []
    public_provider_skipped: list[dict] = []
    public_provider_stub: list[str] = []
    public_provider_errors: list[dict] = []
    public_query_variants: list[str] = []
    public_provider_timeout_count: int = 0
    public_provider_import_error_count: int = 0
    public_discovery_empty_reason: str = ""


# ----------------------------------------------------------------------
# Main entry point — delegates to live_public_pipeline.py for now
# ----------------------------------------------------------------------


async def async_run_live_public_pipeline(
    query: str,
    store=None,
    max_results: int = 10,
    fetch_timeout_s: float = 35.0,
    fetch_max_bytes: int = 2_000_000,
    fetch_concurrency: int = 8,  # F290: 5→8, M1 8GB RAM budget allows 8 concurrent HTTP
    hermes_engine=None,
    graph=None,
    memory_manager=None,
    session_id: str | None = None,
    vector_store=None,
    run_loop: bool = False,
    rl_steps: int = 0,
    enqueue_hypothesis_pivot=None,
    public_bootstrap_enabled: bool = False,
    seed_context=None,
    fetch_fn=None,
    match_fn=None,
    discovery_fn=None,
    ct_subdomains_fn=None,
    clear_query_cache_fn=None,
    export_dir: str | None = None,
    _sprint_id: str = "",
) -> Any:  # temporarily delegates to live_public_pipeline.PipelineRunResult
    """Public pipeline entry point.

    This is a thin wrapper around the monolithic live_public_pipeline.py.
    After the full split is complete, this function will wire together
    public_discovery, public_patterns, public_acceptance, public_fetch,
    public_storage, and public_report modules.

    Currently delegates to the existing monolithic implementation for
    backward compatibility.
    """
    # Import the existing implementation
    from pipeline.live_public_pipeline import async_run_live_public_pipeline as _impl

    return await _impl(
        query=query,
        store=store,
        max_results=max_results,
        fetch_timeout_s=fetch_timeout_s,
        fetch_max_bytes=fetch_max_bytes,
        fetch_concurrency=fetch_concurrency,
        hermes_engine=hermes_engine,
        graph=graph,
        memory_manager=memory_manager,
        session_id=session_id,
        vector_store=vector_store,
        run_loop=run_loop,
        rl_steps=rl_steps,
        enqueue_hypothesis_pivot=enqueue_hypothesis_pivot,
        public_bootstrap_enabled=public_bootstrap_enabled,
        seed_context=seed_context,
        fetch_fn=fetch_fn,
        match_fn=match_fn,
        discovery_fn=discovery_fn,
        ct_subdomains_fn=ct_subdomains_fn,
        clear_query_cache_fn=clear_query_cache_fn,
        export_dir=export_dir,
        _sprint_id=_sprint_id,
    )
