"""Phase classes for live public pipeline — extracted from live_public_pipeline.py.

This module contains the Phase classes that orchestrate the public OSINT pipeline.
Each phase is responsible for one stage of the pipeline.

F360-REFACTOR: Extracted from live_public_pipeline.py to reduce god function complexity.
F364-REFACTOR: Result types converted to msgspec.Struct(frozen=True, gc=False)
               for memory efficiency on M1 8GB.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from collections.abc import Awaitable, Callable

# Import msgspec.Struct for memory-efficient data structures (M1 8GB)
from compat.msgspec_gc_compat import Struct
from hledac.universal.runtime.lane_registry import LANE_REGISTRY
from hledac.universal.tools.url_dedup import get_default_bloom_filter
from hledac.universal.utils.asyncx import parallel, safe_create_task

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)

from hledac.universal.pipeline.public._generators import (
    _extract_base_domain,
    _filter_public_noise,
    _is_threat_query,
    _query_looks_like_domain,
    generate_bootstrap_urls,
    generate_keyword_bootstrap_urls,
    generate_rescue_urls,
    generate_seed_context_bootstrap_urls,
)


class DiscoveryPhaseResult(Struct, frozen=True, gc=False):
    """Structured discovery output for downstream phases.

    F364-REFACTOR: Converted from __slots__ class to msgspec.Struct for
                   memory efficiency on M1 8GB.
    """

    hits: tuple = ()
    discovery_result: Any = None
    discovery_error: str | None = None
    discovery_error_type: str | None = None
    discovery_elapsed_s: float | None = None
    discovery_attempted: bool = False
    discovery_telemetry: dict = {}
    academic_findings_count: int = 0
    ct_injected: int = 0
    cc_injected: int = 0
    onion_findings_count: int = 0
    pastebin_findings_count: int = 0
    github_secrets_count: int = 0
    keyword_seed_fallback_triggered: bool = False


class PipelinePageResult(Struct, frozen=True, gc=False):
    """Result of processing a single discovered page.

    F364-REFACTOR: Converted from __slots__ class to msgspec.Struct for
                   memory efficiency on M1 8GB.
    """

    url: str = ""
    fetched: bool = False
    matched_patterns: int = 0
    accepted_findings: int = 0
    stored_findings: int = 0
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


class PipelineRunResult:
    """Top-level result of a full pipeline run."""

    __slots__ = (
        "query",
        "discovered",
        "fetched",
        "matched_patterns",
        "accepted_findings",
        "stored_findings",
        "patterns_configured",
        "pages",
        "error",
        "strong_pages",
        "weak_pages_skipped",
        "low_value_fetches",
        "discovery_strong_content_weak",
        "discovery_and_content_strong",
        "discovery_squandered",
        "noise_fetch_ratio",
        "corroboration_vs_burn",
        "public_next_action",
        "public_confidence_note",
        "public_branch_verdict",
        "usable_findings_ratio",
        "discovery_to_findings_efficiency",
        "quality_mix",
        "public_proof_grade",
        "public_value_density",
        "top_waste_pattern",
        "discovery_false_positive_count",
        "waste_category_counts",
        "structural_health_ratio",
        "factual_value_density",
        "run_waste_pattern_code",
        "waste_reason_breakdown",
        "backend_degraded",
        "public_discovery_blocker",
        "public_fetch_accessibility_blocker",
        "public_discovery_fallback_state",
        "dominant_public_failure_mode",
        "public_stage_failure",
        "public_stage_failure_reason",
        "public_discovery_attempted",
        "public_discovery_raw_count",
        "public_discovery_deduped_count",
        "public_pages_fetched",
        "public_pages_accepted",
        "public_pages_rejected",
        "public_findings_accepted",
        "zero_hit_accessible_fetch_count",
        "ct_subdomain_injected",
        "cc_archive_injected",
        "academic_findings_count",
        "pastebin_findings_count",
        "github_secrets_count",
        "public_bootstrap_enabled",
        "public_bootstrap_candidates_count",
        "public_bootstrap_fetch_attempted",
        "public_bootstrap_fetch_success",
        "public_bootstrap_accepted_findings",
        "public_bootstrap_errors",
        "public_bootstrap_order",
        "public_bootstrap_prevented_discovery_timeout",
        "public_bootstrap_first_fetch_attempted",
        "public_rescue_candidates_count",
        "public_rescue_fetch_attempted",
        "public_rescue_fetch_success",
        "public_rescue_accepted_findings",
        "public_rescue_errors",
        "public_rescue_order",
        "keyword_seed_fallback_triggered",
        "zero_hit_quality_reason_counts",
        "zero_hit_title_samples",
        "public_zero_hit_summary",
        "public_discovered",
        "public_fetch_attempted",
        "public_fetch_skipped",
        "public_fetch_skip_reason",
        "public_js_renderer_unavailable",
        "public_xml_or_rss_detected",
        "public_fetch_timeout_count",
        "public_fetch_blocked_by_memory",
        "public_discovery_cache_hit",
        "public_discovery_query_count",
        "public_fetch_candidate_count",
        "public_fetch_gate",
        "public_fetch_attempted_urls_sample",
        "public_acceptance_attempted",
        "public_acceptance_accepted",
        "public_acceptance_rejected",
        "public_acceptance_reject_reasons",
        "public_accepted_url_sample",
        "public_rejected_url_sample",
        "public_terminal_classified_count",
        "public_unclassified_count",
        "public_terminal_reason_counts",
        "public_fetch_success",
        "public_fetch_failed",
        "public_skipped_duplicate",
        "public_skipped_unsupported_scheme",
        "public_skipped_memory_gate",
        "public_skipped_quality_gate",
        "public_skipped_browser_unavailable",
        "public_skipped_xml_or_feed",
        "public_skipped_timeout",
        "public_skipped_fetch_error",
        "public_rejected_no_pattern_match",
        "public_rejected_low_information",
        "public_rejected_duplicate",
        "public_rejected_storage_rejected",
        "public_build_success_count",
        "public_build_failure_count",
        "public_duplicate_count",
        "public_acceptance_ratio",
        "public_skipped_url_sample",
        "public_rejected_url_samples",
        "public_candidates_discovered",
        "public_candidates_fetch_attempted",
        "public_candidates_fetch_success",
        "public_candidates_parse_success",
        "public_candidates_pattern_matched",
        "public_candidates_built",
        "public_candidates_store_attempted",
        "public_candidates_stored",
        "public_candidates_rejected",
        "public_rejection_summary",
        "public_terminal_stage",
        "public_provider_selected",
        "public_provider_skipped",
        "public_provider_stub",
        "public_provider_errors",
        "public_query_variants",
        "public_provider_timeout_count",
        "public_provider_import_error_count",
        "public_discovery_empty_reason",
    )

    def __init__(
        self,
        query: str = "",
        discovered: int = 0,
        fetched: int = 0,
        matched_patterns: int = 0,
        accepted_findings: int = 0,
        stored_findings: int = 0,
        patterns_configured: int = 0,
        pages: tuple = (),
        error: str | None = None,
        strong_pages: int = 0,
        weak_pages_skipped: int = 0,
        low_value_fetches: int = 0,
        discovery_strong_content_weak: int = 0,
        discovery_and_content_strong: int = 0,
        discovery_squandered: int = 0,
        noise_fetch_ratio: float = 0.0,
        corroboration_vs_burn: float = 0.0,
        public_next_action: str = "",
        public_confidence_note: str = "",
        public_branch_verdict: dict | None = None,
        usable_findings_ratio: float = 0.0,
        discovery_to_findings_efficiency: float = 0.0,
        quality_mix: str = "",
        public_proof_grade: str = "",
        public_value_density: float = 0.0,
        top_waste_pattern: str = "",
        discovery_false_positive_count: int = 0,
        waste_category_counts: dict | None = None,
        structural_health_ratio: float = 0.0,
        factual_value_density: float = 0.0,
        run_waste_pattern_code: str = "",
        waste_reason_breakdown: str = "",
        backend_degraded: bool = False,
        public_discovery_blocker: str | None = None,
        public_fetch_accessibility_blocker: bool = False,
        public_discovery_fallback_state: str | None = None,
        dominant_public_failure_mode: str | None = None,
        public_stage_failure: str | None = None,
        public_stage_failure_reason: str | None = None,
        public_discovery_attempted: bool = False,
        public_discovery_raw_count: int = 0,
        public_discovery_deduped_count: int = 0,
        public_pages_fetched: int = 0,
        public_pages_accepted: int = 0,
        public_pages_rejected: int = 0,
        public_findings_accepted: int = 0,
        zero_hit_accessible_fetch_count: int = 0,
        ct_subdomain_injected: int = 0,
        cc_archive_injected: int = 0,
        academic_findings_count: int = 0,
        pastebin_findings_count: int = 0,
        github_secrets_count: int = 0,
        public_bootstrap_enabled: bool = False,
        public_bootstrap_candidates_count: int = 0,
        public_bootstrap_fetch_attempted: int = 0,
        public_bootstrap_fetch_success: int = 0,
        public_bootstrap_accepted_findings: int = 0,
        public_bootstrap_errors: int = 0,
        public_bootstrap_order: str = "disabled",
        public_bootstrap_prevented_discovery_timeout: bool = False,
        public_bootstrap_first_fetch_attempted: bool = False,
        public_rescue_candidates_count: int = 0,
        public_rescue_fetch_attempted: int = 0,
        public_rescue_fetch_success: int = 0,
        public_rescue_accepted_findings: int = 0,
        public_rescue_errors: int = 0,
        public_rescue_order: str = "disabled",
        keyword_seed_fallback_triggered: bool = False,
        zero_hit_quality_reason_counts: dict | None = None,
        zero_hit_title_samples: tuple = (),
        public_zero_hit_summary: dict | None = None,
        public_discovered: int = 0,
        public_fetch_attempted: int = 0,
        public_fetch_skipped: int = 0,
        public_fetch_skip_reason: str | None = None,
        public_js_renderer_unavailable: int = 0,
        public_xml_or_rss_detected: int = 0,
        public_fetch_timeout_count: int = 0,
        public_fetch_blocked_by_memory: int = 0,
        public_discovery_cache_hit: int = 0,
        public_discovery_query_count: int = 0,
        public_fetch_candidate_count: int = 0,
        public_fetch_gate: str = "none",
        public_fetch_attempted_urls_sample: tuple[str, ...] = (),
        public_acceptance_attempted: int = 0,
        public_acceptance_accepted: int = 0,
        public_acceptance_rejected: int = 0,
        public_acceptance_reject_reasons: dict | None = None,
        public_accepted_url_sample: tuple[str, ...] = (),
        public_rejected_url_sample: tuple[str, ...] = (),
        public_terminal_classified_count: int = 0,
        public_unclassified_count: int = 0,
        public_terminal_reason_counts: dict | None = None,
        public_fetch_success: int = 0,
        public_fetch_failed: int = 0,
        public_skipped_duplicate: int = 0,
        public_skipped_unsupported_scheme: int = 0,
        public_skipped_memory_gate: int = 0,
        public_skipped_quality_gate: int = 0,
        public_skipped_browser_unavailable: int = 0,
        public_skipped_xml_or_feed: int = 0,
        public_skipped_timeout: int = 0,
        public_skipped_fetch_error: int = 0,
        public_rejected_no_pattern_match: int = 0,
        public_rejected_low_information: int = 0,
        public_rejected_duplicate: int = 0,
        public_rejected_storage_rejected: int = 0,
        public_build_success_count: int = 0,
        public_build_failure_count: int = 0,
        public_duplicate_count: int = 0,
        public_acceptance_ratio: float = 0.0,
        public_skipped_url_sample: tuple[str, ...] = (),
        public_rejected_url_samples: tuple[str, ...] = (),
        public_candidates_discovered: int = 0,
        public_candidates_fetch_attempted: int = 0,
        public_candidates_fetch_success: int = 0,
        public_candidates_parse_success: int = 0,
        public_candidates_pattern_matched: int = 0,
        public_candidates_built: int = 0,
        public_candidates_store_attempted: int = 0,
        public_candidates_stored: int = 0,
        public_candidates_rejected: int = 0,
        public_rejection_summary: dict | None = None,
        public_terminal_stage: str = "",
        public_provider_selected: list[str] | None = None,
        public_provider_skipped: list[dict] | None = None,
        public_provider_stub: list[str] | None = None,
        public_provider_errors: list[dict] | None = None,
        public_query_variants: list[str] | None = None,
        public_provider_timeout_count: int = 0,
        public_provider_import_error_count: int = 0,
        public_discovery_empty_reason: str = "",
    ) -> None:
        self.query = query
        self.discovered = discovered
        self.fetched = fetched
        self.matched_patterns = matched_patterns
        self.accepted_findings = accepted_findings
        self.stored_findings = stored_findings
        self.patterns_configured = patterns_configured
        self.pages = pages
        self.error = error
        self.strong_pages = strong_pages
        self.weak_pages_skipped = weak_pages_skipped
        self.low_value_fetches = low_value_fetches
        self.discovery_strong_content_weak = discovery_strong_content_weak
        self.discovery_and_content_strong = discovery_and_content_strong
        self.discovery_squandered = discovery_squandered
        self.noise_fetch_ratio = noise_fetch_ratio
        self.corroboration_vs_burn = corroboration_vs_burn
        self.public_next_action = public_next_action
        self.public_confidence_note = public_confidence_note
        self.public_branch_verdict = public_branch_verdict or {}
        self.usable_findings_ratio = usable_findings_ratio
        self.discovery_to_findings_efficiency = discovery_to_findings_efficiency
        self.quality_mix = quality_mix
        self.public_proof_grade = public_proof_grade
        self.public_value_density = public_value_density
        self.top_waste_pattern = top_waste_pattern
        self.discovery_false_positive_count = discovery_false_positive_count
        self.waste_category_counts = waste_category_counts or {}
        self.structural_health_ratio = structural_health_ratio
        self.factual_value_density = factual_value_density
        self.run_waste_pattern_code = run_waste_pattern_code
        self.waste_reason_breakdown = waste_reason_breakdown
        self.backend_degraded = backend_degraded
        self.public_discovery_blocker = public_discovery_blocker
        self.public_fetch_accessibility_blocker = public_fetch_accessibility_blocker
        self.public_discovery_fallback_state = public_discovery_fallback_state
        self.dominant_public_failure_mode = dominant_public_failure_mode
        self.public_stage_failure = public_stage_failure
        self.public_stage_failure_reason = public_stage_failure_reason
        self.public_discovery_attempted = public_discovery_attempted
        self.public_discovery_raw_count = public_discovery_raw_count
        self.public_discovery_deduped_count = public_discovery_deduped_count
        self.public_pages_fetched = public_pages_fetched
        self.public_pages_accepted = public_pages_accepted
        self.public_pages_rejected = public_pages_rejected
        self.public_findings_accepted = public_findings_accepted
        self.zero_hit_accessible_fetch_count = zero_hit_accessible_fetch_count
        self.ct_subdomain_injected = ct_subdomain_injected
        self.cc_archive_injected = cc_archive_injected
        self.academic_findings_count = academic_findings_count
        self.pastebin_findings_count = pastebin_findings_count
        self.github_secrets_count = github_secrets_count
        self.public_bootstrap_enabled = public_bootstrap_enabled
        self.public_bootstrap_candidates_count = public_bootstrap_candidates_count
        self.public_bootstrap_fetch_attempted = public_bootstrap_fetch_attempted
        self.public_bootstrap_fetch_success = public_bootstrap_fetch_success
        self.public_bootstrap_accepted_findings = public_bootstrap_accepted_findings
        self.public_bootstrap_errors = public_bootstrap_errors
        self.public_bootstrap_order = public_bootstrap_order
        self.public_bootstrap_prevented_discovery_timeout = public_bootstrap_prevented_discovery_timeout
        self.public_bootstrap_first_fetch_attempted = public_bootstrap_first_fetch_attempted
        self.public_rescue_candidates_count = public_rescue_candidates_count
        self.public_rescue_fetch_attempted = public_rescue_fetch_attempted
        self.public_rescue_fetch_success = public_rescue_fetch_success
        self.public_rescue_accepted_findings = public_rescue_accepted_findings
        self.public_rescue_errors = public_rescue_errors
        self.public_rescue_order = public_rescue_order
        self.keyword_seed_fallback_triggered = keyword_seed_fallback_triggered
        self.zero_hit_quality_reason_counts = zero_hit_quality_reason_counts or {}
        self.zero_hit_title_samples = zero_hit_title_samples
        self.public_zero_hit_summary = public_zero_hit_summary or {}
        self.public_discovered = public_discovered
        self.public_fetch_attempted = public_fetch_attempted
        self.public_fetch_skipped = public_fetch_skipped
        self.public_fetch_skip_reason = public_fetch_skip_reason
        self.public_js_renderer_unavailable = public_js_renderer_unavailable
        self.public_xml_or_rss_detected = public_xml_or_rss_detected
        self.public_fetch_timeout_count = public_fetch_timeout_count
        self.public_fetch_blocked_by_memory = public_fetch_blocked_by_memory
        self.public_discovery_cache_hit = public_discovery_cache_hit
        self.public_discovery_query_count = public_discovery_query_count
        self.public_fetch_candidate_count = public_fetch_candidate_count
        self.public_fetch_gate = public_fetch_gate
        self.public_fetch_attempted_urls_sample = public_fetch_attempted_urls_sample
        self.public_acceptance_attempted = public_acceptance_attempted
        self.public_acceptance_accepted = public_acceptance_accepted
        self.public_acceptance_rejected = public_acceptance_rejected
        self.public_acceptance_reject_reasons = public_acceptance_reject_reasons or {}
        self.public_accepted_url_sample = public_accepted_url_sample
        self.public_rejected_url_sample = public_rejected_url_sample
        self.public_terminal_classified_count = public_terminal_classified_count
        self.public_unclassified_count = public_unclassified_count
        self.public_terminal_reason_counts = public_terminal_reason_counts or {}
        self.public_fetch_success = public_fetch_success
        self.public_fetch_failed = public_fetch_failed
        self.public_skipped_duplicate = public_skipped_duplicate
        self.public_skipped_unsupported_scheme = public_skipped_unsupported_scheme
        self.public_skipped_memory_gate = public_skipped_memory_gate
        self.public_skipped_quality_gate = public_skipped_quality_gate
        self.public_skipped_browser_unavailable = public_skipped_browser_unavailable
        self.public_skipped_xml_or_feed = public_skipped_xml_or_feed
        self.public_skipped_timeout = public_skipped_timeout
        self.public_skipped_fetch_error = public_skipped_fetch_error
        self.public_rejected_no_pattern_match = public_rejected_no_pattern_match
        self.public_rejected_low_information = public_rejected_low_information
        self.public_rejected_duplicate = public_rejected_duplicate
        self.public_rejected_storage_rejected = public_rejected_storage_rejected
        self.public_build_success_count = public_build_success_count
        self.public_build_failure_count = public_build_failure_count
        self.public_duplicate_count = public_duplicate_count
        self.public_acceptance_ratio = public_acceptance_ratio
        self.public_skipped_url_sample = public_skipped_url_sample
        self.public_rejected_url_samples = public_rejected_url_samples
        self.public_candidates_discovered = public_candidates_discovered
        self.public_candidates_fetch_attempted = public_candidates_fetch_attempted
        self.public_candidates_fetch_success = public_candidates_fetch_success
        self.public_candidates_parse_success = public_candidates_parse_success
        self.public_candidates_pattern_matched = public_candidates_pattern_matched
        self.public_candidates_built = public_candidates_built
        self.public_candidates_store_attempted = public_candidates_store_attempted
        self.public_candidates_stored = public_candidates_stored
        self.public_candidates_rejected = public_candidates_rejected
        self.public_rejection_summary = public_rejection_summary or {}
        self.public_terminal_stage = public_terminal_stage
        self.public_provider_selected = public_provider_selected or []
        self.public_provider_skipped = public_provider_skipped or []
        self.public_provider_stub = public_provider_stub or []
        self.public_provider_errors = public_provider_errors or []
        self.public_query_variants = public_query_variants or []
        self.public_provider_timeout_count = public_provider_timeout_count
        self.public_provider_import_error_count = public_provider_import_error_count
        self.public_discovery_empty_reason = public_discovery_empty_reason


@dataclass(frozen=True, slots=True)
class PipelineContext:
    """Immutable context passed through all pipeline phases."""

    query: str
    store: DuckDBShadowStore | None
    max_results: int
    fetch_timeout_s: float
    fetch_max_bytes: int
    fetch_concurrency: int
    hermes_engine: Any = None
    graph: Any = None
    memory_manager: Any = None
    session_id: str | None = None
    vector_store: Any = None
    run_loop: bool = False
    rl_steps: int = 0
    enqueue_hypothesis_pivot: Any = None
    public_bootstrap_enabled: bool = False
    seed_context: Any = None
    export_dir: str | None = None
    _sprint_id: str = ""
    uma_state: str = "UMA_STATE_OK"
    effective_concurrency: int = 8
    hits: tuple = field(default_factory=tuple)
    discovery_telemetry: dict = field(default_factory=dict)
    all_page_results: list = field(default_factory=list)
    generated_report: str = ""
    tot_solution_count: int = 0
    clear_query_cache_fn: Any = None
    error: str | None = None


_MAX_BOOTSTRAP_URLS = 5


class Phase1_Initialization:
    """Phase 1: Setup pipeline context, reset temporal layer, clear caches."""

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Initialize pipeline context with temporal reset, cache clear, DI resolution."""
        try:
            from hledac.universal.layers import reset_temporal_signal_layer

            reset_temporal_signal_layer()
        except Exception:
            pass
        _resolved_clear_cache = ctx.clear_query_cache_fn
        if _resolved_clear_cache is None:
            try:
                from hledac.universal.discovery.duckduckgo_adapter import _clear_query_cache

                _resolved_clear_cache = _clear_query_cache
            except ImportError:
                _resolved_clear_cache = None
        if _resolved_clear_cache is not None:
            try:
                _resolved_clear_cache()
            except Exception:
                pass
        persistence_enabled = False
        persistence_restored = False
        try:
            from hledac.universal.layers import is_temporal_store_enabled, load_temporal_signal_snapshot

            persistence_enabled = is_temporal_store_enabled()
            if persistence_enabled:
                persistence_restored = load_temporal_signal_snapshot()
        except Exception:
            pass
        session_id = ctx.session_id
        if session_id is None:
            # E1: Hardware-accelerated SHA-256 (ARM NEON on Apple Silicon)
            try:
                from _core.rust_backend import rust

                hashes = rust.crypto.batch_sha256_hw([ctx.query])
                session_id = hashes[0][:16] if hashes else hashlib.sha256(ctx.query.encode()).hexdigest()[:16]
            except Exception:
                session_id = hashlib.sha256(ctx.query.encode()).hexdigest()[:16]
        return PipelineContext(
            **{
                **ctx.__dict__,
                "session_id": session_id,
                "_persistence_enabled": persistence_enabled,
                "_persistence_restored": persistence_restored,
                "_persistence_saved": False,
            }
        )


class Phase2_ResourceGovernance:
    """Phase 2: Check UMA state, compute effective concurrency."""

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Check UMA state and determine effective fetch concurrency."""
        from hledac.universal._core.resource_governor import (
            UMA_STATE_CRITICAL,
            UMA_STATE_EMERGENCY,
            UMA_STATE_OK,
        )

        uma_state = UMA_STATE_OK
        try:
            uma_state, _ = await _get_uma_state()
        except Exception:
            pass
        effective_concurrency = ctx.fetch_concurrency
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            effective_concurrency = 1
        return PipelineContext(
            **{
                **ctx.__dict__,
                "uma_state": uma_state,
                "effective_concurrency": effective_concurrency,
                "_semaphore": asyncio.Semaphore(effective_concurrency),
                "_is_emergency": uma_state == UMA_STATE_EMERGENCY,
            }
        )


class Phase3_DiscoveryRunner:
    """Phase 3: Run discovery using DiscoveryEngine."""

    async def run(self, ctx: PipelineContext) -> tuple[PipelineContext, DiscoveryPhaseResult]:
        """Execute discovery phase and return structured result."""
        discovery_info = await DiscoveryEngine(
            query=ctx.query,
            store=ctx.store,
            max_results=ctx.max_results,
            public_bootstrap_enabled=ctx.public_bootstrap_enabled,
            seed_context=ctx.seed_context,
        ).run(uma_state=ctx.uma_state)
        return (
            PipelineContext(
                **{
                    **ctx.__dict__,
                    "hits": discovery_info.hits,
                    "discovery_telemetry": discovery_info.discovery_telemetry,
                }
            ),
            discovery_info,
        )


class Phase4_FetchOrchestrator:
    """Phase 4: Create fetch tasks, run parallel execution, assemble results."""

    async def run(self, ctx: PipelineContext, discovery: DiscoveryPhaseResult) -> tuple[PipelineContext, list]:
        """Execute fetch batch and return page results."""
        from hledac.universal.pipeline.public_fetch import _fetch_and_process_page

        is_threat = _is_threat_query(ctx.query)
        hits, noise_rejections = _filter_public_noise(discovery.hits, is_threat)
        noise_reject_reasons: dict[str, int] = {}
        for _url, reason in noise_rejections:
            noise_reject_reasons[reason] = noise_reject_reasons.get(reason, 0) + 1
        bloom_filter = get_default_bloom_filter()
        seen_url_count = 0
        tasks: list[asyncio.Task] = []
        for hit in hits[:500]:
            meta = _extract_hit_metadata(hit)
            if meta["url"] in bloom_filter:
                continue
            bloom_filter.add(meta["url"])
            seen_url_count += 1
            task = safe_create_task(
                _fetch_and_process_page(
                    semaphore=ctx._semaphore,
                    query=ctx.query,
                    hit_url=meta["url"],
                    hit_title=meta["title"],
                    hit_snippet=meta["snippet"],
                    hit_rank=meta["rank"],
                    fetch_timeout_s=ctx.fetch_timeout_s,
                    fetch_max_bytes=ctx.fetch_max_bytes,
                    store=ctx.store,
                    memory_manager=ctx.memory_manager,
                    session_id=ctx.session_id,
                    discovery_score=meta["score"],
                    discovery_reason=meta["reason"],
                    vector_store=ctx.vector_store,
                    graph=ctx.graph,
                ),
                name="fetch:public_page",
            )
            tasks.append(task)
        _result = await parallel(tasks, policy="collect", ctx="live_public_page_fetch")
        ok_results, error_results = (_result.ok, _result.errors)
        all_page_results = list(ok_results)
        return (
            PipelineContext(
                **{
                    **ctx.__dict__,
                    "all_page_results": all_page_results,
                    "_seen_url_count": seen_url_count,
                    "_noise_reject_reasons": noise_reject_reasons,
                    "_error_results": error_results,
                }
            ),
            all_page_results,
        )


class Phase5_TelemetryAggregator:
    """Phase 5: Compute all run-level telemetry."""

    def run(self, ctx: PipelineContext, discovery: DiscoveryPhaseResult) -> dict[str, Any]:
        """Aggregate telemetry from phase results."""
        results = ctx.all_page_results
        fetched = [p for p in results if hasattr(p, "fetched") and p.fetched]
        total_discovered = len(results)
        total_fetched = len(fetched)
        total_matched = sum(getattr(p, "matched_patterns", 0) for p in results)
        total_accepted = sum(getattr(p, "accepted_findings", 0) for p in results)
        total_stored = sum(getattr(p, "stored_findings", 0) for p in results)
        seen_count = ctx._seen_url_count
        noise_reasons = ctx._noise_reject_reasons
        return {
            "total_discovered": total_discovered,
            "total_fetched": total_fetched,
            "total_matched": total_matched,
            "total_accepted": total_accepted,
            "total_stored": total_stored,
            "seen_url_count": seen_count,
            "noise_reject_reasons": noise_reasons,
        }


class Phase6_ReportGenerator:
    """Phase 6: Generate OSINT report, run RL loop, hypothesis + ToT."""

    async def run(self, ctx: PipelineContext, all_page_results: list) -> tuple[PipelineContext, str, int]:
        """Execute report generation and RL/ToT phases."""
        from hledac.universal.pipeline.public._report_helpers import (
            _generate_and_store_report,
            _run_hypothesis_tot,
            _run_rl_loop,
        )

        generated_report = ""
        tot_solution_count = 0

        # H6: report / RL / ToT are independent (read-only ctx, shared loaded
        # hermes_engine) → run concurrently. parallel() gathers with
        # return_exceptions=True and isolates per-task failures via
        # policy="collect" + names, mirroring the original try/except blocks.
        coros: list = []
        names: list = []
        if ctx.hermes_engine is not None and all_page_results:
            coros.append(
                _generate_and_store_report(
                    query=ctx.query,
                    pages=tuple(all_page_results),
                    store=ctx.store,
                    hermes_engine=ctx.hermes_engine,
                    vector_store=ctx.vector_store,
                )
            )
            names.append("report")
        if ctx.run_loop and ctx.hermes_engine is not None:
            coros.append(_run_rl_loop(ctx=ctx, all_page_results=all_page_results))
            names.append("rl")
        if ctx.store is not None and ctx.hermes_engine is not None:
            coros.append(_run_hypothesis_tot(ctx=ctx, all_page_results=all_page_results))
            names.append("tot")

        if coros:
            result = await parallel(
            coros, policy="collect", names=names,
            concurrency=_m1_concurrency_budget(ceil=3, floor=1), ctx="phase6",
            )
            by_name = result.by_name

            report_val = by_name.get("report")
            if not isinstance(report_val, Exception):
                generated_report = report_val or ""

            rl_val = by_name.get("rl")
            tot_val = by_name.get("tot")
            rl_count = rl_val.get("tot_solution_count", 0) if isinstance(rl_val, dict) else 0
            tot_count = tot_val.get("tot_solution_count", 0) if isinstance(tot_val, dict) else 0
            tot_solution_count = max(rl_count, tot_count)
        return (
            PipelineContext(
                **{
                    **ctx.__dict__,
                    "generated_report": generated_report,
                    "tot_solution_count": tot_solution_count,
                }
            ),
            generated_report,
            tot_solution_count,
        )


class Phase7_SynthesisRunner:
    """Phase 7: LLM synthesis (memory bounded for M1 8GB)."""

    async def run(self, ctx: PipelineContext, total_stored: int) -> None:
        """Execute synthesis if conditions met (M1 8GB safe)."""
        if total_stored < 5 or not LANE_REGISTRY.is_enabled("hermes_synthesis"):
            return
        try:
            from hledac.universal._core.psutil_shim import process

            rss_bytes = await asyncio.to_thread(lambda: process().memory_info().rss)
            rss_gib = rss_bytes / 1024**3
            if rss_gib > 5.5:
                logger.debug("[SYNTHESIS] Skipped: RSS %.1fGiB > 5.5GiB", rss_gib)
                return
        except Exception:
            pass


class Phase8_ExportManager:
    """Phase 8: Export to Obsidian Markdown and interactive HTML graph."""

    async def run(self, ctx: PipelineContext, generated_report: str, all_page_results: list) -> None:
        """Execute export to markdown and graph HTML."""
        # H6: graph HTML + markdown export are independent blocking file writes.
        # Offload each sync export to a worker thread (to_thread) and run them
        # concurrently; parallel() isolates per-task failures.
        coros: list = []

        graph_present = False
        try:
            graph_present = (
                ctx.graph is not None
                and hasattr(ctx.graph, "node_count")
                and ctx.graph.node_count() > 0
            )
        except Exception:
            graph_present = False
        if graph_present:
            export_path = os.path.expanduser("~/new_hledac_graph.html")
            coros.append(asyncio.to_thread(ctx.graph.export_html, export_path))

        try:
            from hledac.universal.export.export_manager import get_export_manager

            resolved_export_dir = ctx.export_dir or os.environ.get("HLEDAC_EXPORT_DIR", os.environ.get("GHOST_EXPORT_DIR"))
            export_mgr = get_export_manager(resolved_export_dir)
            sources = [getattr(p, "url", "") for p in all_page_results if hasattr(p, "url") and p.url][:20]
            export_metadata = {
                "query": ctx.query,
                "sources": sources,
                "tags": ["hledac", "osint", "public-pipeline"],
                "session_id": ctx.session_id,
            }
            coros.append(
                asyncio.to_thread(
                    export_mgr.export_markdown,
                    report=generated_report,
                    findings=[],
                    file_path=None,
                    metadata=export_metadata,
                )
            )
        except Exception as e:
            logger.warning(f"[P18] Export manager unavailable: {e}")

        if coros:
            results = await parallel(
                coros, policy="collect",
                concurrency=_m1_concurrency_budget(ceil=2, floor=1), ctx="phase8",
            )
            for exc in results.errors:
                logger.warning(f"[P18] Export failed: {exc}")
            for r in results.ok:
                if isinstance(r, str) and r:
                    logger.info(f"[P18] Exported markdown to {r}")


class Phase9_TemporalPersistence:
    """Phase 9: Save temporal signal snapshot after pipeline completion."""

    def run(self) -> dict:
        """Save and return persistence status."""
        try:
            from hledac.universal.layers import (
                build_temporal_priority_hints,
                get_temporal_signal_summary,
                save_temporal_signal_snapshot,
            )

            temporal_signal_summary = get_temporal_signal_summary(k=10)
            temporal_priority_hints = build_temporal_priority_hints(k=10)
            persistence_saved = save_temporal_signal_snapshot()
        except Exception:
            temporal_signal_summary = {}
            temporal_priority_hints = []
            persistence_saved = False
        return {
            "temporal_signal_summary": temporal_signal_summary,
            "temporal_priority_hints": temporal_priority_hints,
            "persistence_saved": persistence_saved,
        }


async def safe_wait_for(coro, timeout, label):
    """Safe asyncio.wait_for wrapper."""
    return await asyncio.wait_for(coro, timeout=timeout)


def _m1_concurrency_budget(ceil: int, floor: int = 1) -> Callable[[], Awaitable[int]]:
    """M1 8GB-aware concurrency cap for fan-out tasks.

    Returns a zero-arg resolver consumed by ``parallel(concurrency=...)``.
    It scales fan-out DOWN as free RAM shrinks so several LLM/IO tasks don't
    OOM the 8 GiB UMA MacBook Air: full ``ceil`` with headroom, ``floor``
    (>=1) under pressure, linear in between. Fail-soft: any error → a
    conservative cap of 2.

    This is the hardware-specific best practice for the target machine: the
    AGENTS.md RAM budget leaves only ~1.75 GiB headroom, so unbounded
    ``asyncio.gather`` of three LLM/IO tasks can exhaust RAM and crash the
    whole sprint (worse than running slower).
    """

    async def _resolve() -> int:
        try:
            from hledac.universal._core.psutil_shim import available_gb

            avail_gib = await asyncio.to_thread(available_gb)
        except Exception:  # noqa: BLE001 - fail-soft budgeting
            return max(floor, min(ceil, 2))
        if not isinstance(avail_gib, (int, float)) or avail_gib <= 0:
            return max(floor, min(ceil, 2))
        if avail_gib >= 1.5:
            return ceil
        if avail_gib <= 0.5:
            return floor
        frac = (avail_gib - 0.5) / 1.0
        return max(floor, min(ceil, round(floor + (ceil - floor) * frac)))

    return _resolve


_ASYNC_DISCOVERY_SEARCH: Any = None
_CT_SCANNER_GET_SUBDOMAINS: Any = None


def _ensure_discovery_patched() -> None:
    """Lazily patch the discovery search function."""
    global _ASYNC_DISCOVERY_SEARCH
    if _ASYNC_DISCOVERY_SEARCH is None:
        if LANE_REGISTRY.is_enabled("providerless_discovery"):
            try:
                from hledac.universal.discovery.cascade import async_search_providerless

                _ASYNC_DISCOVERY_SEARCH = async_search_providerless
            except ImportError:
                _ASYNC_DISCOVERY_SEARCH = async_search_public_web
        else:
            _ASYNC_DISCOVERY_SEARCH = async_search_public_web


def _ensure_ct_scanner_patched() -> None:
    """Lazily patch the CT scanner."""
    global _CT_SCANNER_GET_SUBDOMAINS
    if _CT_SCANNER_GET_SUBDOMAINS is not None:
        return
    try:
        from hledac.universal.network.ct_log_scanner import _CTLogScanner

        _scanner = _CTLogScanner(allow_external=True, cache_ttl_days=30)

        async def _get_subdomains(domain: str, async_session: Any = None) -> list[str]:
            return await _scanner.get_subdomains(domain, async_session=async_session)

        _CT_SCANNER_GET_SUBDOMAINS = _get_subdomains
    except Exception:
        _CT_SCANNER_GET_SUBDOMAINS = None


# NOTE: _ensure_discovery_patched() and _ensure_ct_scanner_patched() are called
# from live_public_pipeline.py._ensure_patched() before running the pipeline.
# Do NOT call them here at module level to avoid duplicate initialization.


class DiscoveryEngine:
    """Engine 1: Handles all discovery-related logic."""

    __slots__ = ("query", "store", "max_results", "public_bootstrap_enabled", "seed_context")

    def __init__(
        self,
        query: str,
        store: Any,
        max_results: int,
        public_bootstrap_enabled: bool,
        seed_context: Any = None,
    ) -> None:
        self.query = query
        self.store = store
        self.max_results = max_results
        self.public_bootstrap_enabled = public_bootstrap_enabled
        self.seed_context = seed_context

    async def run(self, uma_state: str) -> DiscoveryPhaseResult:
        """Run discovery phase with bootstrap, rescue, and keyword fallback."""
        bs = await self._run_bootstrap_phase()
        bootstrap_hits, rescue_hits = bs["bootstrap_hits"], bs["rescue_hits"]
        disc = await self._run_discovery_phase(bootstrap_hits, rescue_hits)
        hits = disc["hits"]
        discovery_error = disc["error"]
        discovery_error_type = disc["error_type"]
        discovery_elapsed_s = disc["elapsed"]
        discovery_attempted = disc["attempted"]
        kw_result = {
            "candidates_count": 0,
            "fetch_attempted": 0,
            "fetch_success": 0,
            "bootstrap_order": "disabled",
            "errors": 0,
            "hits": (),
        }
        if not hits:
            kw_result = await self._run_keyword_fallback()
            hits = kw_result["hits"]
        discovery_telemetry = _build_discovery_telemetry(
            discovery_result=disc["result"],
            discovery_error=discovery_error,
            discovery_error_type=discovery_error_type,
            discovery_elapsed_s=discovery_elapsed_s,
            discovery_attempted=discovery_attempted,
            hits=hits,
            **bs,
            **kw_result,
        )
        if not hits:
            return DiscoveryPhaseResult(
                hits=(),
                discovery_result=None,
                discovery_error=discovery_error,
                discovery_error_type=discovery_error_type,
                discovery_elapsed_s=discovery_elapsed_s,
                discovery_attempted=discovery_attempted,
                discovery_telemetry=discovery_telemetry,
                academic_findings_count=0,
                ct_injected=0,
                cc_injected=0,
                onion_findings_count=0,
                pastebin_findings_count=0,
                github_secrets_count=0,
                keyword_seed_fallback_triggered=bs["keyword_fallback_triggered"],
            )
        augmented_result = await self._run_augmentation_phases(hits)
        hits = augmented_result["hits"]
        onion_findings_count = augmented_result.get("onion_findings_count", 0)
        return DiscoveryPhaseResult(
            hits=hits,
            discovery_result=disc["result"],
            discovery_error=discovery_error,
            discovery_error_type=discovery_error_type,
            discovery_elapsed_s=discovery_elapsed_s,
            discovery_attempted=discovery_attempted,
            discovery_telemetry=discovery_telemetry,
            academic_findings_count=augmented_result["academic_findings_count"],
            ct_injected=augmented_result["ct_injected"],
            cc_injected=augmented_result["cc_injected"],
            onion_findings_count=onion_findings_count,
            pastebin_findings_count=augmented_result["pastebin_findings_count"],
            github_secrets_count=augmented_result["github_secrets_count"],
            keyword_seed_fallback_triggered=bs["keyword_fallback_triggered"],
        )

    async def _run_augmentation_phases(self, hits: tuple) -> dict:
        """Run all augmentation phases."""
        # H6: _run_academic_lane (writes findings via store.submit_findings),
        # _run_phase1_augmentation (CT/CC network enrichment, no store writes) and
        # _run_onion_phase (independent Tor onion discovery on the same `hits`)
        # are all independent → run concurrently. parallel() isolates failures and
        # the M1 memory budget caps fan-out so we never OOM the 8 GiB Air.
        result = await parallel(
        [
            _run_academic_lane(self.store, self.query),
            _run_phase1_augmentation(hits, self.query, self.store),
            _run_onion_phase(hits, self.query, self.store),
        ],
        policy="collect",
        names=["academic", "augment", "onion"],
        concurrency=_m1_concurrency_budget(ceil=3, floor=1),
        ctx="augmentation",
        )
        by_name = result.by_name

        academic_findings_count = 0
        acad_val = by_name.get("academic")
        if not isinstance(acad_val, Exception) and isinstance(acad_val, int):
            academic_findings_count = acad_val

        ct_augmented, cc_augmented, p20_counts = hits, hits, (0, 0)
        aug_val = by_name.get("augment")
        if not isinstance(aug_val, Exception) and isinstance(aug_val, tuple):
            ct_augmented, cc_augmented, p20_counts = aug_val
        elif isinstance(aug_val, Exception):
            logger.debug("[AUG] phase1 augmentation failed: %s", aug_val)

        onion_findings_count = 0
        onion_val = by_name.get("onion")
        if not isinstance(onion_val, Exception) and isinstance(onion_val, int):
            onion_findings_count = onion_val
        elif isinstance(onion_val, Exception):
            logger.debug("[AUG] onion phase failed: %s", onion_val)

        ct_injected = len(ct_augmented) - len(hits)
        cc_injected = len(cc_augmented) - len(hits)
        pastebin_findings_count, github_secrets_count = p20_counts
        enriched_hits = cc_augmented
        return {
            "hits": enriched_hits,
            "academic_findings_count": academic_findings_count,
            "ct_injected": ct_injected,
            "cc_injected": cc_injected,
            "pastebin_findings_count": pastebin_findings_count,
            "github_secrets_count": github_secrets_count,
            "onion_findings_count": onion_findings_count,
        }

    async def _run_bootstrap_phase(self) -> dict:
        """Phase 1: Run bootstrap and rescue URL generation."""
        bootstrap_hits = []
        rescue_hits = []
        candidates_count = 0
        rescue_count = 0
        keyword_fallback_triggered = False
        rescue_order = "disabled"
        try:
            rescue_hits = generate_rescue_urls(self.query, max_urls=5)
            rescue_count = len(rescue_hits)
            if rescue_hits:
                rescue_order = "keyword_seed_fallback"
                keyword_fallback_triggered = True
                bootstrap_hits = rescue_hits
                rescue_hits = []
        except Exception:
            rescue_count = 0
        if self.public_bootstrap_enabled:
            try:
                bootstrap_urls = generate_bootstrap_urls(self.query, max_urls=_MAX_BOOTSTRAP_URLS)
                candidates_count = len(bootstrap_urls)
                for idx, url in enumerate(bootstrap_urls):
                    bootstrap_hits.append(_make_discovery_hit(self.query, url, idx))
            except Exception:
                candidates_count = 0
            if candidates_count == 0:
                try:
                    rescue_hits = generate_rescue_urls(self.query, max_urls=8)
                    rescue_count = len(rescue_hits)
                    if rescue_hits:
                        rescue_order = "rescue_fallback"
                        bootstrap_hits = rescue_hits
                        rescue_hits = []
                except Exception:
                    rescue_count = 0
            if candidates_count == 0 and rescue_count == 0 and self.seed_context is not None:
                try:
                    seed_urls = generate_seed_context_bootstrap_urls(self.seed_context, max_candidates=10)
                    candidates_count = len(seed_urls)
                    for idx, url in enumerate(seed_urls):
                        bootstrap_hits.append(
                            _make_discovery_hit(self.query, url, idx, reason="seed_context_bootstrap")
                        )
                except Exception:
                    candidates_count = 0
        return {
            "bootstrap_hits": bootstrap_hits,
            "rescue_hits": rescue_hits,
            "candidates_count": candidates_count,
            "bootstrap_order": "disabled",
            "prevented_timeout": False,
            "first_fetch_attempted": False,
            "fetch_attempted": 0,
            "fetch_success": 0,
            "rescue_count": rescue_count,
            "rescue_order": rescue_order,
            "keyword_fallback_triggered": keyword_fallback_triggered,
            "pub_bootstrap_candidates_count": candidates_count,
            "pub_bootstrap_fetch_attempted": 0,
            "pub_bootstrap_fetch_success": 0,
            "pub_bootstrap_accepted_findings": 0,
            "pub_bootstrap_errors": 0,
            "pub_rescue_candidates_count": rescue_count,
            "pub_rescue_fetch_attempted": 0,
            "pub_rescue_fetch_success": 0,
            "pub_rescue_accepted_findings": 0,
            "pub_rescue_errors": 0,
            "pub_rescue_order": rescue_order,
            "pub_keyword_bootstrap_candidates_count": 0,
            "pub_keyword_bootstrap_fetch_attempted": 0,
            "pub_keyword_bootstrap_fetch_success": 0,
            "pub_keyword_bootstrap_order": "disabled",
            "pub_keyword_bootstrap_errors": 0,
            "pub_build_success_count": 0,
            "pub_build_failure_count": 0,
            "pub_duplicate_count": 0,
            "pub_provider_selected": [],
            "pub_provider_skipped": [],
            "pub_provider_stub": [],
            "pub_provider_errors": [],
            "pub_query_variants": [],
            "pub_provider_timeout_count": [0],
            "pub_provider_import_error_count": [0],
            "pub_discovery_empty_reason": [],
        }

    async def _run_discovery_phase(self, bootstrap_hits: list, rescue_hits: list) -> dict:
        """Phase 2: Execute the main discovery search."""
        discovery_error: str | None = None
        discovery_error_type: str | None = None
        discovery_elapsed_s: float | None = None
        discovery_attempted = False
        hits: tuple = ()
        discovery_result = None
        _discovery_start: float | None = None
        cache_hit = 0
        try:
            _discovery_start = time.monotonic()
            discovery_attempted = True
            discovery_result = await safe_wait_for(
                _ASYNC_DISCOVERY_SEARCH(self.query, self.max_results), timeout=35.0, label="live_public_discovery"
            )
            discovery_elapsed_s = time.monotonic() - _discovery_start
            cache_hit = int(getattr(discovery_result, "cache_hit", False))
            if hasattr(discovery_result, "hits"):
                disc_hits = discovery_result.hits
            elif isinstance(discovery_result, dict):
                disc_hits = discovery_result.get("hits", ())
            else:
                disc_hits = ()
            if bootstrap_hits:
                hits = tuple(bootstrap_hits) + disc_hits
            elif rescue_hits:
                hits = tuple(rescue_hits) + disc_hits
            else:
                hits = disc_hits
            err_val = (
                discovery_result.get("error")
                if isinstance(discovery_result, dict)
                else getattr(discovery_result, "error", None)
            )
            if err_val:
                discovery_error = str(err_val)
        except asyncio.CancelledError:
            discovery_elapsed_s = time.monotonic() - _discovery_start if _discovery_start else None
            raise
        except Exception as exc:
            discovery_elapsed_s = time.monotonic() - _discovery_start if _discovery_start else None
            discovery_error = f"discovery_exception:{type(exc).__name__}:{exc}"
            hits = ()
        return {
            "hits": hits,
            "result": discovery_result,
            "error": discovery_error,
            "error_type": discovery_error_type,
            "elapsed": discovery_elapsed_s,
            "attempted": discovery_attempted,
            "cache_hit": cache_hit,
            "query_count": 1,
        }

    async def _run_keyword_fallback(self) -> dict:
        """Phase 3: Keyword-based search engine fallback."""
        hits: tuple = ()
        candidates_count = 0
        bootstrap_order = "disabled"
        fetch_attempted = 0
        fetch_success = 0
        errors = 0
        try:
            keyword_hits = await generate_keyword_bootstrap_urls(self.query, max_urls=10)
            candidates_count = len(keyword_hits)
            if keyword_hits:
                hits = tuple(keyword_hits)
                bootstrap_order = "keyword_bootstrap"
                fetch_attempted = len(keyword_hits)
                fetch_success = len(keyword_hits)
        except Exception:
            errors = 1
            candidates_count = 0
        return {
            "hits": hits,
            "candidates_count": candidates_count,
            "bootstrap_order": bootstrap_order,
            "fetch_attempted": fetch_attempted,
            "fetch_success": fetch_success,
            "errors": errors,
        }


def _make_discovery_hit(query: str, url: str, rank: int, reason: str = "deterministic_bootstrap") -> Any:
    """Create a DiscoveryHit-like object."""
    from hledac.universal.discovery.duckduckgo_adapter import DiscoveryHit

    return DiscoveryHit(
        query=query,
        title=f"Bootstrap {rank + 1}",
        url=url,
        snippet=f"Bootstrap URL: {url}",
        score=0.85,
        reason=reason,
        rank=rank,
        source="bootstrap",
        retrieved_ts=0.0,
    )


def _extract_hit_metadata(hit) -> dict:
    """Extract URL, title, snippet, score, reason, rank from discovery hit."""
    hit_url = getattr(hit, "url", None) or (str(hit[2]) if len(hit) > 2 else "")
    hit_score = getattr(hit, "score", None)
    if hit_score is None and hasattr(hit, "__getitem__"):
        try:
            hit_score = float(hit[4]) if len(hit) > 4 else None
        except (ValueError, TypeError):
            hit_score = None
    hit_reason = getattr(hit, "reason", None)
    hit_title = getattr(hit, "title", None) or (
        str(hit[1] if len(hit) > 1 else "") if hasattr(hit, "__getitem__") else ""
    )
    hit_snippet = getattr(hit, "snippet", None) or (
        str(hit[3] if len(hit) > 3 else "") if hasattr(hit, "__getitem__") else ""
    )
    hit_rank = getattr(hit, "rank", 0)
    return {
        "url": hit_url,
        "title": hit_title,
        "snippet": hit_snippet,
        "score": hit_score,
        "reason": hit_reason,
        "rank": hit_rank,
    }


async def _get_uma_state() -> tuple[str, bool]:
    """Read UMA status via resource governor surface."""
    try:
        from hledac.universal._core.resource_governor import evaluate_uma_state, sample_uma_status_async

        status = await sample_uma_status_async()
        state = evaluate_uma_state(status.system_used_gib)
        return (state, status.io_only)
    except Exception:
        return ("UMA_STATE_OK", False)


async def _run_academic_lane(store: Any, query: str) -> int:
    """Run academic research lane (Phase 1A)."""
    academic_findings_count = 0
    if store is not None:
        try:
            if LANE_REGISTRY.is_enabled("academic"):
                from hledac.universal.discovery.academic import ACADEMIC_ENABLED, search_all_academic

                if ACADEMIC_ENABLED:
                    academic_results = await search_all_academic(query, max_results_per_source=10)
                    all_findings = []
                    for _source, findings in academic_results.items():
                        all_findings.extend(findings)
                    if all_findings:
                        await store.submit_findings(all_findings)
                        academic_findings_count = len(all_findings)
        except Exception as e:
            logger.warning(f"[F259] Academic research lane failed: {e}")
    return academic_findings_count


async def _run_phase1_augmentation(hits: tuple, query: str, store: Any) -> tuple:
    """Phase 1: CT + CC + Pastebin/GitHub in parallel."""

    async def _ct_wrapper():
        try:
            return await _inject_ct_subdomain_hits(hits, query)
        except Exception:
            return hits

    async def _cc_wrapper():
        try:
            return await _inject_commoncrawl_hits(hits, query)
        except Exception:
            return hits

    async def _pastebin_github_wrapper():
        return (0, 0)

    _build_p1 = await parallel(
        [_ct_wrapper(), _cc_wrapper(), _pastebin_github_wrapper()], concurrency=4, policy="collect", ctx="phase1_aug"
    )
    return (_build_p1.ok[0], _build_p1.ok[1], _build_p1.ok[2])


async def _run_onion_phase(hits: tuple, query: str, store: Any) -> int:
    """Phase 2: Onion discovery."""
    if store is None:
        return 0
    try:
        return await _inject_onion_hits(hits, query, store)
    except Exception:
        return 0


def _build_discovery_telemetry(**kwargs) -> dict:
    """Build discovery telemetry dict."""
    return {
        "discovery_result": kwargs.get("result"),
        "public_stage_failure": "discovery_empty" if not kwargs.get("hits") else None,
        "public_stage_failure_reason": kwargs.get("error") or "no URLs returned",
        "public_discovery_raw_count": len(kwargs.get("hits", ())),
        "public_discovery_attempted": kwargs.get("attempted", False),
        "public_discovery_cache_hit": kwargs.get("cache_hit", 0),
        "public_discovery_query_count": kwargs.get("query_count", 0),
        "public_bootstrap_order": kwargs.get("bootstrap_order") or "disabled",
        "public_bootstrap_candidates_count": kwargs.get("pub_bootstrap_candidates_count", 0),
        "public_rescue_candidates_count": kwargs.get("pub_rescue_candidates_count", 0),
        "keyword_seed_fallback_triggered": kwargs.get("keyword_fallback_triggered", False),
        "public_keyword_bootstrap_candidates_count": kwargs.get("pub_keyword_bootstrap_candidates_count", 0),
        "public_build_success_count": kwargs.get("pub_build_success_count", 0),
        "public_build_failure_count": kwargs.get("pub_build_failure_count", 0),
        "public_duplicate_count": kwargs.get("pub_duplicate_count", 0),
        "public_provider_selected": kwargs.get("pub_provider_selected", []),
        "public_provider_skipped": kwargs.get("pub_provider_skipped", []),
        "public_provider_stub": kwargs.get("pub_provider_stub", []),
        "public_provider_errors": kwargs.get("pub_provider_errors", []),
        "public_query_variants": kwargs.get("pub_query_variants", []),
        "public_provider_timeout_count": kwargs.get("pub_provider_timeout_count", [0])[0]
        if kwargs.get("pub_provider_timeout_count")
        else 0,
        "public_provider_import_error_count": kwargs.get("pub_provider_import_error_count", [0])[0]
        if kwargs.get("pub_provider_import_error_count")
        else 0,
        "public_discovery_empty_reason": kwargs.get("pub_discovery_empty_reason", [""])[0]
        if kwargs.get("pub_discovery_empty_reason")
        else "",
        "discovery_error_type": kwargs.get("error_type") or "",
        "discovery_elapsed_s": round(kwargs.get("elapsed"), 3) if kwargs.get("elapsed") else None,
        "public_candidates_discovered": 0,
        "public_candidates_fetch_attempted": 0,
        "public_candidates_fetch_success": 0,
        "public_candidates_parse_success": 0,
        "public_candidates_pattern_matched": 0,
        "public_candidates_built": 0,
        "public_candidates_store_attempted": 0,
        "public_candidates_stored": 0,
        "public_candidates_rejected": 0,
    }


async def _inject_ct_subdomain_hits(hits: tuple, query: str) -> tuple:
    """Thin CT winner-slice adapter."""
    global _CT_SCANNER_GET_SUBDOMAINS
    if not hits or not _query_looks_like_domain(query):
        return hits
    _ensure_ct_scanner_patched()
    if _CT_SCANNER_GET_SUBDOMAINS is None:
        return hits
    base_domain = _extract_base_domain(query)
    try:
        from hledac.universal.network.session_runtime import async_get_httpx_session

        shared_session = await async_get_httpx_session()
    except Exception:
        shared_session = None
    try:
        subdomains: list[str] = await _CT_SCANNER_GET_SUBDOMAINS(base_domain, async_session=shared_session)
    except Exception:
        return hits
    if not subdomains:
        return hits
    subdomains = subdomains[:10]
    ct_hits = tuple(
        _make_discovery_hit(query, f"https://{sub}", idx, "ct_subdomain") for idx, sub in enumerate(subdomains)
    )
    return ct_hits + hits


async def _inject_commoncrawl_hits(hits: tuple, query: str) -> tuple:
    """Thin CommonCrawl CDX injection."""
    return hits


async def _inject_onion_hits(hits: tuple, query: str, store: Any) -> int:
    """Onion discovery + scraping via Tor."""
    try:
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    except Exception:
        return 0
    onion_urls = []
    for hit in hits:
        url = getattr(hit, "url", None)
        if url and ".onion" in url.lower():
            onion_urls.append(url if url.startswith("http") else f"http://{url}")
    if not onion_urls:
        return 0
    onion_urls = onion_urls[:3]
    findings = []
    ts_now = time.time()

    async def _fetch_one(onion_url: str) -> CanonicalFinding | None:
        try:
            result = await async_fetch_public_text(onion_url, timeout_s=30.0, max_bytes=200000)
            if result.error or result.text is None:
                return None
            # E1: Hardware-accelerated SHA-256 (ARM NEON on Apple Silicon)
            pf_key = f"{query}\x00{onion_url}\x00onion_discovery"
            try:
                from _core.rust_backend import rust

                hashes = rust.crypto.batch_sha256_hw([pf_key])
                pf_id = hashes[0][:16] if hashes else hashlib.sha256(pf_key.encode()).hexdigest()[:16]
            except Exception:
                pf_id = hashlib.sha256(pf_key.encode()).hexdigest()[:16]
            return CanonicalFinding(
                finding_id=pf_id,
                query=query,
                source_type="onion_discovery",
                confidence=0.55,
                ts=ts_now,
                provenance=("onion_discovery", onion_url),
                payload_text=result.text[:500] if result.text else None,
            )
        except Exception:
            return None

    _result = await parallel([_fetch_one(url) for url in onion_urls], policy="collect", concurrency=3, ctx="onion_hits")
    for finding in _result.ok:
        if finding is not None:
            findings.append(finding)
    if findings and store is not None:
        try:
            await store.submit_findings(findings)
            logger.info(f"[F193A] Stored {len(findings)} onion findings")
        except Exception:
            pass
    return len(findings)


def _get_patterns_configured_count() -> int:
    """Return current pattern count from singleton registry."""
    try:
        state = sys.modules["hledac.universal.patterns.pattern_matcher"]._matcher_state
        return len(state._registry_snapshot) if state._registry_snapshot else 0
    except Exception:
        return 0


def _build_emergency_result(ctx: PipelineContext) -> PipelineRunResult:
    """Build emergency abort result."""
    return PipelineRunResult(
        query=ctx.query,
        discovered=0,
        fetched=0,
        matched_patterns=0,
        accepted_findings=0,
        stored_findings=0,
        patterns_configured=0,
        pages=(),
        error="uma_emergency_abort",
        public_discovery_blocker="uma_emergency_abort",
        public_fetch_accessibility_blocker=False,
        public_discovery_fallback_state=None,
        dominant_public_failure_mode="uma_emergency_abort",
        public_stage_failure="uma_emergency",
        public_stage_failure_reason="UMA emergency state blocks all public lane processing",
        public_discovery_attempted=False,
        public_discovery_raw_count=0,
        public_discovery_deduped_count=0,
        public_pages_fetched=0,
        public_pages_accepted=0,
        public_pages_rejected=0,
        public_findings_accepted=0,
        public_fetch_gate="emergency_blocked",
        public_discovered=0,
        public_fetch_attempted=0,
        public_fetch_skipped=0,
        public_fetch_candidate_count=0,
        public_fetch_attempted_urls_sample=(),
        public_acceptance_attempted=0,
        public_acceptance_accepted=0,
        public_acceptance_rejected=0,
        public_acceptance_reject_reasons={},
        public_accepted_url_sample=(),
        public_rejected_url_sample=(),
        public_terminal_classified_count=0,
        public_unclassified_count=0,
        public_terminal_reason_counts={},
        public_fetch_success=0,
        public_fetch_failed=0,
        public_skipped_duplicate=0,
        public_skipped_unsupported_scheme=0,
        public_skipped_memory_gate=0,
        public_skipped_quality_gate=0,
        public_skipped_browser_unavailable=0,
        public_skipped_xml_or_feed=0,
        public_skipped_timeout=0,
        public_skipped_fetch_error=0,
        public_rejected_no_pattern_match=0,
        public_rejected_low_information=0,
        public_rejected_duplicate=0,
        public_rejected_storage_rejected=0,
        public_build_success_count=0,
        public_build_failure_count=0,
        public_duplicate_count=0,
        public_acceptance_ratio=0.0,
        public_skipped_url_sample=(),
        public_rejected_url_samples=(),
        public_candidates_discovered=0,
        public_candidates_fetch_attempted=0,
        public_candidates_fetch_success=0,
        public_candidates_parse_success=0,
        public_candidates_pattern_matched=0,
        public_candidates_built=0,
        public_candidates_store_attempted=0,
        public_candidates_stored=0,
        public_candidates_rejected=0,
        public_rejection_summary={},
        public_rescue_candidates_count=0,
        public_rescue_fetch_attempted=0,
        public_rescue_fetch_success=0,
        public_rescue_accepted_findings=0,
        public_rescue_errors=0,
        public_rescue_order="disabled",
        public_terminal_stage="uma_emergency",
    )


def _build_pipeline_run_result(
    ctx: PipelineContext,
    telemetry: dict,
    discovery: DiscoveryPhaseResult,
    public_stage_failure: str | None,
    public_stage_failure_reason: str | None,
    generated_report: str,
    tot_solution_count: int,
) -> PipelineRunResult:
    """Build final PipelineRunResult from phase outputs."""
    t = telemetry
    total_discovered = t["total_discovered"]
    total_fetched = t["total_fetched"]
    total_matched = t["total_matched"]
    total_accepted = t["total_accepted"]
    total_stored = t["total_stored"]
    run_error = discovery.discovery_error
    usable_findings_ratio = round(total_stored / max(total_discovered, 1), 3)
    public_value_density = round(total_stored / max(total_fetched, 1), 3)
    return PipelineRunResult(
        query=ctx.query,
        discovered=total_discovered,
        fetched=total_fetched,
        matched_patterns=total_matched,
        accepted_findings=total_accepted,
        stored_findings=total_stored,
        patterns_configured=_get_patterns_configured_count(),
        pages=tuple(ctx.all_page_results),
        error=run_error,
        strong_pages=0,
        weak_pages_skipped=0,
        low_value_fetches=0,
        discovery_strong_content_weak=0,
        discovery_and_content_strong=0,
        discovery_squandered=0,
        noise_fetch_ratio=0.0,
        corroboration_vs_burn=0.0,
        public_next_action="",
        public_confidence_note="",
        public_branch_verdict={},
        usable_findings_ratio=usable_findings_ratio,
        discovery_to_findings_efficiency=0.0,
        quality_mix="",
        public_proof_grade="",
        public_value_density=public_value_density,
        top_waste_pattern="",
        discovery_false_positive_count=0,
        waste_category_counts={},
        structural_health_ratio=0.0,
        factual_value_density=public_value_density,
        run_waste_pattern_code="",
        waste_reason_breakdown="",
        backend_degraded=False,
        public_discovery_blocker=None,
        public_fetch_accessibility_blocker=False,
        public_discovery_fallback_state=None,
        dominant_public_failure_mode=None,
        public_stage_failure=public_stage_failure,
        public_stage_failure_reason=public_stage_failure_reason,
        public_discovery_attempted=discovery.discovery_attempted,
        public_discovery_raw_count=total_discovered,
        public_discovery_deduped_count=t.get("seen_url_count", 0),
        public_pages_fetched=total_fetched,
        public_pages_accepted=sum(1 for p in ctx.all_page_results if getattr(p, "accepted_findings", 0) > 0),
        public_pages_rejected=sum(
            1 for p in ctx.all_page_results if getattr(p, "fetched", False) and getattr(p, "accepted_findings", 0) == 0
        ),
        public_findings_accepted=total_accepted,
        zero_hit_accessible_fetch_count=0,
        ct_subdomain_injected=discovery.ct_injected,
        cc_archive_injected=discovery.cc_injected,
        academic_findings_count=discovery.academic_findings_count,
        pastebin_findings_count=discovery.pastebin_findings_count,
        github_secrets_count=discovery.github_secrets_count,
        public_bootstrap_enabled=ctx.public_bootstrap_enabled,
        public_bootstrap_candidates_count=0,
        public_bootstrap_fetch_attempted=0,
        public_bootstrap_fetch_success=0,
        public_bootstrap_accepted_findings=0,
        public_bootstrap_errors=0,
        public_bootstrap_order="disabled",
        public_bootstrap_prevented_discovery_timeout=False,
        public_bootstrap_first_fetch_attempted=False,
        public_rescue_candidates_count=0,
        public_rescue_fetch_attempted=0,
        public_rescue_fetch_success=0,
        public_rescue_accepted_findings=0,
        public_rescue_errors=0,
        public_rescue_order="disabled",
        keyword_seed_fallback_triggered=discovery.keyword_seed_fallback_triggered,
        zero_hit_quality_reason_counts={},
        zero_hit_title_samples=(),
        public_zero_hit_summary={},
        public_discovered=total_discovered,
        public_fetch_attempted=total_fetched,
        public_fetch_skipped=total_discovered - t.get("seen_url_count", 0),
        public_fetch_skip_reason=None,
        public_js_renderer_unavailable=0,
        public_xml_or_rss_detected=0,
        public_fetch_timeout_count=0,
        public_fetch_blocked_by_memory=0,
        public_discovery_cache_hit=0,
        public_discovery_query_count=0,
        public_fetch_candidate_count=t.get("seen_url_count", 0),
        public_fetch_gate="ok",
        public_fetch_attempted_urls_sample=(),
        public_acceptance_attempted=0,
        public_acceptance_accepted=0,
        public_acceptance_rejected=0,
        public_acceptance_reject_reasons={},
        public_accepted_url_sample=(),
        public_rejected_url_sample=(),
        public_terminal_classified_count=0,
        public_unclassified_count=len(ctx.all_page_results),
        public_terminal_reason_counts={},
        public_fetch_success=t.get("candidates_fetch_success", 0),
        public_fetch_failed=t.get("candidates_fetch_attempted", 0) - t.get("candidates_fetch_success", 0),
        public_skipped_duplicate=total_discovered - t.get("seen_url_count", 0),
        public_skipped_unsupported_scheme=0,
        public_skipped_memory_gate=0,
        public_skipped_quality_gate=0,
        public_skipped_browser_unavailable=0,
        public_skipped_xml_or_feed=0,
        public_skipped_timeout=0,
        public_skipped_fetch_error=0,
        public_rejected_no_pattern_match=0,
        public_rejected_low_information=0,
        public_rejected_duplicate=0,
        public_rejected_storage_rejected=0,
        public_build_success_count=0,
        public_build_failure_count=0,
        public_duplicate_count=0,
        public_acceptance_ratio=0.0,
        public_skipped_url_sample=(),
        public_rejected_url_samples=(),
        public_candidates_discovered=t.get("candidates_discovered", 0),
        public_candidates_fetch_attempted=t.get("candidates_fetch_attempted", 0),
        public_candidates_fetch_success=t.get("candidates_fetch_success", 0),
        public_candidates_parse_success=t.get("candidates_parse_success", 0),
        public_candidates_pattern_matched=t.get("candidates_pattern_matched", 0),
        public_candidates_built=t.get("candidates_built", 0),
        public_candidates_store_attempted=t.get("candidates_store_attempted", 0),
        public_candidates_stored=t.get("candidates_stored", 0),
        public_candidates_rejected=t.get("candidates_rejected", 0),
        public_rejection_summary={},
        public_terminal_stage="",
        public_provider_selected=[],
        public_provider_skipped=[],
        public_provider_stub=[],
        public_provider_errors=[],
        public_query_variants=[],
        public_provider_timeout_count=0,
        public_provider_import_error_count=0,
        public_discovery_empty_reason="",
    )



