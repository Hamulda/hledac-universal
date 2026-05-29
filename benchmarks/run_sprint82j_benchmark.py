"""
Sprint 82J: Real End-to-End Benchmark

Runtime validation for:
- Whole-run timing (phase-by-phase)
- Per-layer acquisition stats
- Gating / admission metrics
- Lane metrics
- Memory / thermal stats
- Synthesis metrics
- Bottleneck diagnosis
- Tool execution log summary
- Evidence log summary
- Metrics registry summary

This is NOT a unit test - this is a REAL profiling benchmark.

Sprint 82K Upgrade: Unified observability - reads from real log files
to provide correlated end-to-end picture of a research run.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

logger = logging.getLogger(__name__)


# Benchmark configuration
BENCHMARK_SMOKE_SECONDS = 120  # 2 min smoke test
BENCHMARK_FULL_SECONDS = 600   # 10 min full profiling

# Sprint 7E: Offline benchmark constants
OFFLINE_RUN_DURATION_S = 300
OFFLINE_SMOKE_DURATION_S = 10
CHECKPOINT_INTERVAL_S = 30
BENCHMARK_LOG_PATH = "benchmark_300s.jsonl"
BENCHMARK_SUMMARY_PATH = "benchmark_300s_summary.json"
_HLEDAC_OFFLINE = os.environ.get("HLEDAC_OFFLINE", "0") == "1"


@dataclass
class BenchmarkConfig:
    """Configuration for benchmark run."""
    duration_seconds: int = BENCHMARK_FULL_SECONDS
    query: str = "artificial intelligence future trends 2025"
    depth: str | None = None
    output_dir: Path = field(default_factory=lambda: Path("./benchmark_results"))
    verbose: bool = False
    track_memory: bool = True
    track_thermal: bool = True
    # Sprint 7E: Silent benchmark harness
    mode: str = "SYNTHETIC_MOCK"
    silent: bool = False
    jsonl_path: str = ""
    summary_path: str = ""


@dataclass
class BenchmarkPhaseMetrics:
    """Metrics for a single phase."""
    phase_name: str = ""
    enter_time: float = 0.0
    leave_time: float = 0.0
    duration_seconds: float = 0.0
    promotion_reason: str = ""
    candidates_processed: int = 0
    rejects: int = 0
    holds: int = 0
    admits: int = 0
    promotes: int = 0
    drops: int = 0
    kills: int = 0
    deep_reads: int = 0


@dataclass
class BenchmarkLaneMetrics:
    """Per-lane metrics."""
    lane_id: str = ""
    role: str = ""
    actions: int = 0
    budget_spent: float = 0.0
    contradictions: int = 0
    kills: int = 0
    yields: int = 0


@dataclass
class BenchmarkMemoryMetrics:
    """Memory metrics during benchmark."""
    rss_start_mb: float = 0.0
    rss_peak_mb: float = 0.0
    rss_before_synthesis_mb: float = 0.0
    rss_after_synthesis_mb: float = 0.0
    memory_delta_synthesis_mb: float = 0.0
    memory_pressure_warn_count: int = 0
    memory_pressure_critical_count: int = 0
    forced_throttles: int = 0
    cleanup_requests: int = 0
    mlx_cache_clears: int = 0


@dataclass
class BenchmarkAcquisitionMetrics:
    """Per-layer acquisition metrics."""
    # CT Discovery
    ct_attempts: int = 0
    ct_successes: int = 0
    ct_failures: int = 0
    ct_timeouts: int = 0
    ct_candidates: int = 0

    # Wayback Quick
    wayback_quick_attempts: int = 0
    wayback_quick_successes: int = 0
    wayback_quick_failures: int = 0
    wayback_quick_latency_avg_ms: float = 0.0

    # Wayback CDX
    wayback_cdx_attempts: int = 0
    wayback_cdx_lines: int = 0
    wayback_cdx_early_stop: int = 0
    wayback_cdx_dedup_hits: int = 0
    wayback_cdx_latency_avg_ms: float = 0.0

    # Common Crawl
    commoncrawl_attempts: int = 0
    commoncrawl_lines: int = 0
    commoncrawl_early_stop: int = 0
    commoncrawl_latency_avg_ms: float = 0.0

    # Necromancer
    necromancer_attempts: int = 0
    necromancer_rescues: int = 0
    necromancer_failures: int = 0

    # PRF
    prf_invocations: int = 0
    prf_expansion_terms: int = 0
    prf_expanded_queries: int = 0

    # Onion
    onion_preflight: int = 0
    onion_available: int = 0
    onion_unavailable: int = 0
    onion_skipped: int = 0


@dataclass
class BenchmarkGatingMetrics:
    """Admission / gating metrics."""
    l0_rejects: int = 0
    l1_echo_rejects: int = 0
    l2_holds: int = 0
    l3_head_fallbacks: int = 0
    admits: int = 0
    backlog_pushes: int = 0
    backlog_promotions: int = 0
    backlog_expiries: int = 0
    backlog_evictions: int = 0
    avg_admission_score: float = 0.0
    max_admission_score: float = 0.0
    deepening_gate_candidates: int = 0


@dataclass
class BenchmarkSynthesisMetrics:
    """Final synthesis metrics."""
    invoked: bool = False
    fallback_used: bool = False
    final_context_chars: int = 0
    final_context_claims: int = 0
    knapsack_selected: int = 0
    knapsack_dropped: int = 0
    latency_seconds: float = 0.0
    claims_emitted: int = 0
    contested_claims: int = 0
    contradictions_surfaced: int = 0
    gap_check_invocations: int = 0
    gap_check_hits: int = 0
    schema_validation_success: bool = False
    winner_only_evidence_count: int = 0


@dataclass
class BenchmarkToolExecSummary:
    """Summary of tool execution log (from real JSONL file)."""
    total_events: int = 0
    success_count: int = 0
    error_count: int = 0
    cancelled_count: int = 0
    top_tools: dict[str, int] = field(default_factory=dict)
    first_event_ts: str | None = None
    last_event_ts: str | None = None
    chain_head: str = ""
    chain_valid: bool = True
    errors: list[str] = field(default_factory=list)


@dataclass
class BenchmarkEvidenceSummary:
    """Summary of evidence log (from real SQLite/JSONL)."""
    total_events: int = 0
    event_types: dict[str, int] = field(default_factory=dict)
    tool_call_count: int = 0
    observation_count: int = 0
    synthesis_count: int = 0
    error_count: int = 0
    decision_count: int = 0
    evidence_packet_count: int = 0
    first_event_ts: str | None = None
    last_event_ts: str | None = None


@dataclass
class BenchmarkMetricsSummary:
    """Summary of metrics registry (from real JSONL file)."""
    total_samples: int = 0
    counter_count: int = 0
    gauge_count: int = 0
    expected_counters: list[str] = field(default_factory=list)
    found_counters: list[str] = field(default_factory=list)
    missing_counters: list[str] = field(default_factory=list)
    first_flush_ts: str | None = None
    last_flush_ts: str | None = None


@dataclass
class BenchmarkResults:
    """Complete benchmark results."""
    # Timing - Sprint 8B: truthful timing breakdown
    total_wall_clock_seconds: float = 0.0
    research_runtime_seconds: float = 0.0
    research_loop_elapsed_s: float = 0.0  # actual loop-only time
    synthesis_elapsed_s: float = 0.0  # synthesis post-processing
    teardown_elapsed_s: float = 0.0  # cleanup/shutdown
    # Sprint 8F: Teardown substeps
    teardown_stop_collector_s: float = 0.0
    teardown_cleanup_s: float = 0.0  # Sprint 8H: orch.cleanup() time
    teardown_metrics_extract_s: float = 0.0
    teardown_trace_flush_s: float = 0.0
    teardown_report_write_s: float = 0.0
    teardown_summary_write_s: float = 0.0
    post_loop_live_tasks_count: int = 0
    post_loop_live_task_names: list[str] = field(default_factory=list)
    time_to_first_finding_seconds: float = 0.0
    time_to_first_high_confidence_seconds: float = 0.0
    time_to_first_deep_read_seconds: float = 0.0
    time_to_first_synthesis_seconds: float = 0.0
    final_synthesis_duration_seconds: float = 0.0

    # Sprint 8B: Yield and loop efficiency counters
    sleep0_count: int = 0  # cooperative yields (asyncio.sleep(0))
    idle_sleep_count: int = 0  # idle backoff sleeps (asyncio.sleep(N))

    # Sprint 8B: Echo rejection rate
    echo_rejection_rate: float = 0.0  # l1_echo_rejects / (l1_echo_rejects + admits)

    # Phase metrics
    phases: list[BenchmarkPhaseMetrics] = field(default_factory=list)

    # Lane metrics
    lanes: list[BenchmarkLaneMetrics] = field(default_factory=list)

    # Memory metrics
    memory: BenchmarkMemoryMetrics = field(default_factory=BenchmarkMemoryMetrics)

    # Acquisition metrics
    acquisition: BenchmarkAcquisitionMetrics = field(default_factory=BenchmarkAcquisitionMetrics)

    # Gating metrics
    gating: BenchmarkGatingMetrics = field(default_factory=BenchmarkGatingMetrics)

    # Synthesis metrics
    synthesis: BenchmarkSynthesisMetrics = field(default_factory=BenchmarkSynthesisMetrics)

    # Thermal
    thermal_state_start: str = "unknown"
    thermal_state_peak: str = "unknown"

    # Metadata
    iterations: int = 0
    findings_count: int = 0
    sources_count: int = 0
    # Sprint 8H: Additional truth fields
    data_mode: str = "SYNTHETIC_MOCK"
    total_wall_clock_s: float = 0.0
    hh_index: float = 0.0

    # Sprint 82K: Log/metrics summaries (from real files)
    tool_exec: BenchmarkToolExecSummary = field(default_factory=BenchmarkToolExecSummary)
    evidence: BenchmarkEvidenceSummary = field(default_factory=BenchmarkEvidenceSummary)
    metrics: BenchmarkMetricsSummary = field(default_factory=BenchmarkMetricsSummary)

    # Sprint 82M: Real vs Mock detection
    real_orchestrator: bool = False  # True if real orchestrator was used
    mock_path_used: bool = False  # True if mock fallback was used
    research_entered: bool = False  # True if research() was actually called
    init_error: str = ""  # Error message if initialization failed

    # Run correlation
    run_id: str = ""

    # Sprint 82Q: Early-exit diagnosis
    research_loop_entered: bool = False
    exit_reason: str = ""
    stagnation_reason: str = ""
    actions_considered_count: int = 0
    actions_selected_count: int = 0
    actions_executed_count: int = 0
    source_add_attempts: int = 0
    source_add_successes: int = 0
    premature_stagnation: bool = False
    stagnation_after_iterations: int = 0
    stagnation_after_seconds: float = 0.0
    frontier_empty: bool = False

    # Sprint 82Q Phase 2: Iteration trace and capability reachability
    iteration_trace: dict[str, Any] = field(default_factory=dict)
    capability_reachability: dict[str, Any] = field(default_factory=dict)

    # Sprint 86: Network Recon Economics
    network_recon_precondition_met_count: int = 0
    network_recon_precondition_rejected_scanned: int = 0
    network_recon_precondition_rejected_budget: int = 0
    network_recon_precondition_met_but_not_selected_count: int = 0
    network_recon_selected_count: int = 0
    network_recon_executed_count: int = 0
    network_recon_success_count: int = 0
    network_recon_partial_success_count: int = 0
    network_recon_empty_count: int = 0
    network_recon_candidates_generated: int = 0
    network_recon_candidates_forwarded: int = 0
    network_recon_candidates_dropped_queue_full: int = 0
    network_recon_queue_had_items_at_score_time: int = 0
    network_recon_queue_empty_at_score_time: int = 0
    network_recon_queue_size_avg: float = 0.0
    network_recon_yield_ratio: float | None = None
    network_recon_forwarding_efficiency: float | None = None
    network_recon_selection_rate_pct: float = 0.0

    # Sprint 8C: Per-action echo telemetry
    action_echo_telemetry: dict[str, dict[str, int]] = field(default_factory=dict)

    # Sprint 86F: Wildcard metrics
    network_recon_wildcard_hit_count: int = 0
    network_recon_wildcard_miss_count: int = 0
    network_recon_wildcard_hit_rate: float = 0.0
    network_recon_subdomains_found_before_gate_total: int = 0
    network_recon_subdomains_suppressed_by_wildcard_total: int = 0
    network_recon_wildcard_but_has_mx_ns_txt_findings_count: int = 0

    # Sprint 86F: Score history percentiles (post-mortem)
    network_recon_score_p50: float | None = None
    network_recon_score_p90: float | None = None

    action_selection_hhi: float = 0.0
    action_selection_counts: dict[str, int] = field(default_factory=dict)

    # Sprint 7C: FPS metriky (iterations/findings/sources per second)
    benchmark_fps: float = 0.0  # iterations / elapsed_s
    findings_fps: float = 0.0  # findings / elapsed_s
    sources_fps: float = 0.0   # sources / elapsed_s
    p95_latency_ms: float = 0.0


    # Sprint 7E: Silent benchmark harness checkpoint writing
    def _write_checkpoint(self, orch: Any, elapsed_s: float, jsonl_path: str) -> None:
        """Write a JSONL checkpoint record asynchronously."""
        try:
            # Extract metrics from orchestrator
            iterations = 0
            findings_count = 0
            sources_count = 0
            benchmark_fps = 0.0
            findings_fps = 0.0
            sources_fps = 0.0
            p95_latency_ms = 0.0
            state_cache_hit_rate = 0.0
            data_mode = getattr(self._orch, '_data_mode', 'SYNTHETIC_MOCK')

            if hasattr(orch, '_iteration_counter'):
                iterations = orch._iteration_counter
            if hasattr(orch, '_research_mgr') and orch._research_mgr:
                findings_count = len(getattr(orch._research_mgr, '_findings_heap', []))
                sources_count = len(getattr(orch._research_mgr, '_sources_heap', []))
            if hasattr(orch, '_state_cache_hit_rate'):
                state_cache_hit_rate = orch._state_cache_hit_rate
            if hasattr(orch, '_benchmark_metrics_cache'):
                metrics = orch._benchmark_metrics_cache
                benchmark_fps = metrics.get('benchmark_fps', 0.0)
                findings_fps = metrics.get('findings_fps', 0.0)
                sources_fps = metrics.get('sources_fps', 0.0)
                p95_latency_ms = metrics.get('p95_latency_ms', 0.0)
            if hasattr(orch, '_data_mode'):
                data_mode = orch._data_mode

            # Sprint 7C: Calculate FPS from elapsed
            if elapsed_s > 0:
                benchmark_fps = iterations / elapsed_s
                findings_fps = findings_count / elapsed_s
                sources_fps = sources_count / elapsed_s

            checkpoint = {
                "elapsed_s": elapsed_s,
                "iterations": iterations,
                "benchmark_fps": benchmark_fps,
                "findings_total": findings_count,
                "findings_fps": findings_fps,
                "sources_total": sources_count,
                "sources_fps": sources_fps,
                "HHI": self.results.action_selection_hhi,
                "p95_latency_ms": p95_latency_ms,
                "state_cache_hit_rate": state_cache_hit_rate,
                "data_mode": data_mode,
                "action_echo_telemetry": getattr(orch, '_action_echo_telemetry', {}),
            }

            # Write via asyncio.to_thread to not block event loop
            def _write():
                with open(jsonl_path, "a") as f:
                    f.write(json.dumps(checkpoint) + "\n")

            asyncio.get_event_loop().run_in_executor(None, _write)
        except Exception as e:
            logger.warning(f"Failed to write checkpoint: {e}")


class E2EBenchmark:
    """
    Real E2E Benchmark for Sprint 82J

    This benchmark:
    - Uses the REAL orchestrator flow (research() method)
    - Collects real timing, memory, thermal metrics
    - Provides bottleneck diagnosis
    - Is bounded and memory-safe
    """

    def __init__(self, config: BenchmarkConfig):
        self.config = config
        self.results = BenchmarkResults()
        self._start_time: float = 0.0
        self._first_finding_time: float | None = None
        self._first_high_confidence_time: float | None = None
        self._first_deep_read_time: float | None = None
        self._synthesis_start_time: float | None = None
        self._rss_samples: list[float] = []
        self._phase_timings: dict[str, float] = {}

    def _get_rss_mb(self) -> float:
        """Get current RSS in MB."""
        try:
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0

    async def _setup_orchestrator(self) -> Any:
        """Initialize the orchestrator for benchmark.

        Sprint 82M: Attempts to create real orchestrator first, falls back to mock
        only if real orchestrator cannot be initialized. Always reports which path was used.
        """
        import time as time_module
        from pathlib import Path

        # Sprint 82M: Try real orchestrator first (with timeout)
        logger.info("Attempting to create REAL FullyAutonomousOrchestrator...")

        try:
            from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
            from hledac.universal.config import ResearchMode, UniversalConfig

            config = UniversalConfig.for_mode(ResearchMode.AUTONOMOUS)
            real_orch = FullyAutonomousOrchestrator(config)

            logger.info("Real orchestrator created, calling initialize()...")

            # Sprint 82N: Increased timeout to 30s for full initialization
            init_task = asyncio.create_task(real_orch.initialize())
            try:
                init_result = await asyncio.wait_for(init_task, timeout=30.0)
                if not init_result:
                    logger.warning("⚠️ Real orchestrator init returned False")
                    logger.warning("Falling back to mock orchestrator")
                    self.results.real_orchestrator = False
                    self.results.mock_path_used = True
                    self.results.init_error = "Init returned False"
                    raise RuntimeError("Real orchestrator init returned False")
            except TimeoutError:
                logger.warning("⚠️ Real orchestrator init timed out after 30s")
                logger.warning("Falling back to mock orchestrator")
                self.results.real_orchestrator = False
                self.results.mock_path_used = True
                self.results.init_error = "Init timeout (30s)"
                # Continue with mock
                raise TimeoutError("Real orchestrator init timed out")

            logger.info("✅ REAL orchestrator initialized successfully!")
            self.results.real_orchestrator = True
            self.results.mock_path_used = False
            self.results.run_id = real_orch._run_id
            return real_orch

        except Exception as e:
            import traceback
            error_msg = f"Real orchestrator init failed: {e}"
            logger.error(f"❌ {error_msg}")
            logger.error(f"Traceback: {traceback.format_exc()}")

            # Sprint 82M: Fall back to mock only with explicit warning
            logger.warning("⚠️ Falling back to MinimalBenchmarkOrchestrator (MOCK)")
            logger.warning("This is NOT a real benchmark run - results will be limited!")

            self.results.real_orchestrator = False
            self.results.mock_path_used = True
            self.results.init_error = str(e)[:200]  # Truncate long errors

            # Continue with minimal mock orchestrator
            logger.info("Creating minimal benchmark orchestrator...")

            # Create a simple mock class that provides the benchmark interface
            class MinimalBenchmarkOrchestrator:
                """Minimal orchestrator for benchmark testing."""
                def __init__(self, config):
                    self.config = config
                    self._run_id = f"mock_{int(time_module.time())}"
                    self._sprint_state = {
                        "confirmed": [],
                        "falsified": [],
                        "open_gaps": [],
                        "gate_l0_reject": 0,
                        "gate_l1_echo": 0,
                        "gate_l2_hold": 0,
                        "gate_admit": 0,
                        "backlog_evictions": 0,
                        "backlog_expiries": 0,
                        "deep_read_winner": 0,
                        "deep_read_falsification": 0,
                        "ct_discovery_attempts": 0,
                        "wayback_quick_attempts": 0,
                        "wayback_cdx_attempts": 0,
                        "commoncrawl_attempts": 0,
                        "necromancer_attempts": 0,
                        "prf_expansions": 0,
                        "onion_attempts": 0,
                    }
                    self._initialized = True

                    # Initialize metrics/logging components
                    from hledac.universal.metrics_registry import MetricsRegistry
                    run_dir = Path.home() / '.hledac' / 'runs'
                    run_dir.mkdir(parents=True, exist_ok=True)
                    self._metrics_registry = MetricsRegistry(run_dir=run_dir, run_id=self._run_id)

                    from hledac.universal.tool_exec_log import ToolExecLog
                    self._tool_exec_log = ToolExecLog(run_dir=run_dir, run_id=self._run_id)

                    from hledac.universal.evidence_log import EvidenceLog
                    self._evidence_log = EvidenceLog(run_id=self._run_id)

                    logger.info(f"✅ Minimal orchestrator created with run_id={self._run_id}")

                async def research(self, query: str, timeout: int = 600):
                    """Run research - simplified version."""
                    logger.info(f"Starting research: query='{query}', timeout={timeout}s")
                    start_time = time_module.time()

                    # Simulate some basic research activity
                    self._sprint_state["gate_l0_reject"] = 0
                    self._sprint_state["gate_admit"] = 0

                    # Record metrics during the run
                    elapsed = time_module.time() - start_time
                    logger.info(f"Research completed after {elapsed:.1f}s")

                    # Return minimal result
                    class Result:
                        findings = []
                        statistics = {"iterations": 0}
                        total_sources_checked = 0

                    return Result()

                def cleanup(self):
                    """Cleanup resources."""
                    if hasattr(self, '_metrics_registry') and self._metrics_registry:
                        self._metrics_registry.flush(force=True)
                        self._metrics_registry.close()

            from hledac.universal.config import ResearchMode, UniversalConfig
            config = UniversalConfig.for_mode(ResearchMode.AUTONOMOUS)
            return MinimalBenchmarkOrchestrator(config)

    def _extract_sprint_state_metrics(self, orch: Any) -> None:
        """Extract metrics from orchestrator's _sprint_state."""
        if not hasattr(orch, '_sprint_state'):
            return

        state = orch._sprint_state

        # Gating metrics
        self.results.gating.l0_rejects = state.get('gate_l0_reject', 0)
        self.results.gating.l1_echo_rejects = state.get('gate_l1_echo', 0)
        self.results.gating.l2_holds = state.get('gate_l2_hold', 0)
        self.results.gating.admits = state.get('gate_admit', 0)
        self.results.gating.backlog_evictions = state.get('backlog_evictions', 0)
        self.results.gating.backlog_expiries = state.get('backlog_expiries', 0)

        # Acquisition metrics
        self.results.acquisition.ct_attempts = state.get('ct_discovery_attempts', 0)
        self.results.acquisition.wayback_quick_attempts = state.get('wayback_quick_attempts', 0)
        self.results.acquisition.wayback_cdx_attempts = state.get('wayback_cdx_attempts', 0)
        self.results.acquisition.commoncrawl_attempts = state.get('commoncrawl_attempts', 0)
        self.results.acquisition.necromancer_attempts = state.get('necromancer_attempts', 0)
        self.results.acquisition.prf_invocations = state.get('prf_expansions', 0)
        self.results.acquisition.onion_preflight = state.get('onion_attempts', 0)

        # Deep reads
        self.results.gating.deepening_gate_candidates = state.get('deep_read_winner', 0) + state.get('deep_read_falsification', 0)

        # Sprint 82Q: Early-exit diagnosis metrics
        self.results.research_loop_entered = state.get('research_loop_entered', False)
        self.results.exit_reason = state.get('exit_reason', '')
        self.results.stagnation_reason = state.get('stagnation_reason', '')
        self.results.actions_considered_count = state.get('actions_considered_count', 0)
        self.results.actions_selected_count = state.get('actions_selected_count', 0)
        self.results.actions_executed_count = state.get('actions_executed_count', 0)
        self.results.source_add_attempts = state.get('source_add_attempts', 0)
        self.results.source_add_successes = state.get('source_add_successes', 0)
        self.results.premature_stagnation = state.get('premature_stagnation', False)
        self.results.stagnation_after_iterations = state.get('stagnation_after_iterations', 0)
        self.results.stagnation_after_seconds = state.get('stagnation_after_seconds', 0.0)
        self.results.frontier_empty = state.get('frontier_empty', False)

        # Sprint 8C: Per-action echo telemetry
        if hasattr(orch, '_action_echo_telemetry'):
            self.results.action_echo_telemetry = orch._action_echo_telemetry

        # Sprint 8C: Yield counters (unconditional cooperative yield)
        self.results.sleep0_count = getattr(orch, '_sleep0_count', 0)
        self.results.idle_sleep_count = getattr(orch, '_idle_sleep_count', 0)

        # Sprint 86: Network recon economics metrics
        self.results.network_recon_precondition_met_count = getattr(orch, '_network_recon_precondition_met_count', 0)
        self.results.network_recon_precondition_rejected_scanned = getattr(orch, '_network_recon_precondition_rejected_scanned', 0)
        self.results.network_recon_precondition_rejected_budget = getattr(orch, '_network_recon_precondition_rejected_budget', 0)
        self.results.network_recon_precondition_met_but_not_selected_count = getattr(orch, '_network_recon_precondition_met_but_not_selected_count', 0)
        self.results.network_recon_selected_count = getattr(orch, '_network_recon_selected_count', 0)
        self.results.network_recon_executed_count = getattr(orch, '_network_recon_executed_count', 0)
        self.results.network_recon_success_count = getattr(orch, '_network_recon_success_count', 0)
        self.results.network_recon_partial_success_count = getattr(orch, '_network_recon_partial_success_count', 0)
        self.results.network_recon_empty_count = getattr(orch, '_network_recon_empty_count', 0)
        self.results.network_recon_candidates_generated = getattr(orch, '_network_recon_candidates_generated', 0)
        self.results.network_recon_candidates_forwarded = getattr(orch, '_network_recon_candidates_forwarded', 0)
        self.results.network_recon_candidates_dropped_queue_full = getattr(orch, '_network_recon_candidates_dropped_queue_full', 0)
        # Sprint 86E-R2: Queue diagnostics
        self.results.network_recon_queue_had_items_at_score_time = getattr(orch, '_network_recon_queue_had_items_at_score_time', 0)
        self.results.network_recon_queue_empty_at_score_time = getattr(orch, '_network_recon_queue_empty_at_score_time', 0)
        queue_samples = getattr(orch, '_network_recon_queue_size_samples', [])
        if queue_samples:
            self.results.network_recon_queue_size_avg = sum(queue_samples) / len(queue_samples)

        # Calculate derived metrics
        success_count = self.results.network_recon_success_count
        if success_count > 0:
            # yield_ratio = (findings_contributed + candidates_forwarded) / success_count
            # findings_contributed is 0 because network_recon doesn't produce findings
            self.results.network_recon_yield_ratio = self.results.network_recon_candidates_forwarded / success_count
        else:
            self.results.network_recon_yield_ratio = None

        # forwarding_efficiency = candidates_forwarded / candidates_generated
        generated = self.results.network_recon_candidates_generated
        if generated > 0:
            self.results.network_recon_forwarding_efficiency = self.results.network_recon_candidates_forwarded / generated
        else:
            self.results.network_recon_forwarding_efficiency = None

        # Sprint 86F: Wildcard metrics extraction
        wildcard_hit = getattr(orch, '_network_recon_wildcard_hit_count', 0)
        wildcard_miss = getattr(orch, '_network_recon_wildcard_miss_count', 0)
        self.results.network_recon_wildcard_hit_count = wildcard_hit
        self.results.network_recon_wildcard_miss_count = wildcard_miss
        # wildcard_hit_rate = hit / max(hit + miss, 1)
        total_wildcard_checks = wildcard_hit + wildcard_miss
        if total_wildcard_checks > 0:
            self.results.network_recon_wildcard_hit_rate = wildcard_hit / total_wildcard_checks
        else:
            self.results.network_recon_wildcard_hit_rate = 0.0

        self.results.network_recon_subdomains_found_before_gate_total = getattr(orch, '_network_recon_subdomains_found_before_gate_total', 0)
        self.results.network_recon_subdomains_suppressed_by_wildcard_total = getattr(orch, '_network_recon_subdomains_suppressed_by_wildcard_total', 0)
        self.results.network_recon_wildcard_but_has_mx_ns_txt_findings_count = getattr(orch, '_network_recon_wildcard_but_has_mx_ns_txt_findings_count', 0)

        # Sprint 86F: Score history percentiles (post-mortem)
        score_history = getattr(orch, '_score_history', {})
        if score_history and 'network_recon' in score_history:
            scores = score_history['network_recon']
            if len(scores) >= 30:
                sorted_scores = sorted(scores)
                n = len(sorted_scores)
                self.results.network_recon_score_p50 = sorted_scores[int(n * 0.5)]
                self.results.network_recon_score_p90 = sorted_scores[int(n * 0.9)]
            # If less than 30 samples, leave as None

        # Calculate action selection HHI
        action_counts = getattr(orch, '_action_selection_counts', {})
        total_selections = sum(action_counts.values()) if action_counts else 0
        if total_selections > 0:
            hhi = 0.0
            for count in action_counts.values():
                share = count / total_selections
                hhi += share * share
            self.results.action_selection_hhi = hhi
            self.results.action_selection_counts = action_counts
        else:
            self.results.action_selection_hhi = 0.0
            self.results.action_selection_counts = {}

        # selection_rate_pct = selected_count / total_action_selections * 100
        if total_selections > 0:
            self.results.network_recon_selection_rate_pct = (self.results.network_recon_selected_count / total_selections) * 100
        else:
            self.results.network_recon_selection_rate_pct = 0.0

    def _extract_trace_metrics(self, orch: Any) -> None:
        """Extract iteration trace and capability reachability metrics."""
        # Extract from trace buffer if available
        if hasattr(orch, '_iteration_trace_buffer') and orch._iteration_trace_buffer:
            buffer = orch._iteration_trace_buffer
            total_iterations = len(buffer)

            # Calculate aggregates from trace
            action_selection_count: dict[str, int] = {}
            result_type_counts: dict[str, int] = {}
            total_findings = 0
            total_sources = 0
            query_changed_count = 0
            consecutive_same_action_max = 0

            for trace in buffer:
                action_selection_count[trace.chosen_action] = action_selection_count.get(trace.chosen_action, 0) + 1
                result_type_counts[trace.action_result_type] = result_type_counts.get(trace.action_result_type, 0) + 1
                total_findings += trace.new_findings
                total_sources += trace.new_sources
                if trace.query_changed:
                    query_changed_count += 1
                consecutive_same_action_max = max(consecutive_same_action_max, trace.consecutive_same_action)

            # Store in results
            self.results.iteration_trace = {
                'total_iterations': total_iterations,
                'action_selection_count': action_selection_count,
                'result_type_counts': result_type_counts,
                'total_findings': total_findings,
                'total_sources': total_sources,
                'query_changed_ratio': query_changed_count / max(1, total_iterations),
                'consecutive_same_action_max': consecutive_same_action_max,
            }

        # Compute capability reachability report if available
        if hasattr(orch, '_compute_capability_reachability_report'):
            try:
                self.results.capability_reachability = orch._compute_capability_reachability_report()
            except Exception as e:
                logger.warning(f"Failed to compute capability reachability: {e}")

    def _extract_phase_metrics(self, orch: Any) -> None:
        """Extract phase timing metrics."""
        if not hasattr(orch, '_phase_controller'):
            return

        pc = orch._phase_controller
        self.results.phases = [
            BenchmarkPhaseMetrics(
                phase_name=pc.current_phase.name if hasattr(pc, 'current_phase') else "unknown",
                duration_seconds=pc.elapsed_time if hasattr(pc, 'elapsed_time') else 0.0,
            )
        ]

    def _extract_lane_metrics(self, orch: Any) -> None:
        """Extract per-lane metrics."""
        if not hasattr(orch, '_lane_manager') or not hasattr(orch, '_lane_roles'):
            return

        for lane in orch._lane_manager.active_lanes:
            role = orch._lane_roles.get(lane.lane_id, "unknown")
            lane_metrics = BenchmarkLaneMetrics(
                lane_id=lane.lane_id,
                role=role,
                actions=lane.metrics.iterations if hasattr(lane, 'metrics') else 0,
                budget_spent=lane.metrics.findings_yield if hasattr(lane, 'metrics') else 0.0,
                contradictions=lane.metrics.independent_contradictions if hasattr(lane, 'metrics') else 0,
            )
            self.results.lanes.append(lane_metrics)

    def _extract_tool_exec_summary(self, orch: Any) -> None:
        """Extract tool execution log summary from real JSONL file."""
        if not hasattr(orch, '_tool_exec_log') or not orch._tool_exec_log:
            logger.warning("No ToolExecLog found - skipping tool exec summary")
            return

        try:
            # Get stats from the live object
            stats = orch._tool_exec_log.get_stats()
            self.results.tool_exec.total_events = stats.get('seq', 0)
            self.results.tool_exec.chain_head = stats.get('head_hash', '')

            # Try to read from file for detailed stats
            tool_exec_file = Path.home() / '.hledac' / 'runs' / 'logs' / 'tool_exec.jsonl'
            if not tool_exec_file.exists():
                logger.info(f"Tool exec log file not found: {tool_exec_file}")
                return

            # Stream-parse JSONL (bounded - max 1000 lines)
            tool_counter: dict[str, int] = {}
            first_ts: str | None = None
            last_ts: str | None = None

            with open(tool_exec_file) as f:
                for i, line in enumerate(f):
                    if i >= 1000:  # Bounded read
                        break
                    try:
                        event = json.loads(line)
                        tool_name = event.get('tool_name', 'unknown')
                        tool_counter[tool_name] = tool_counter.get(tool_name, 0) + 1

                        status = event.get('status', '')
                        if status == 'success':
                            self.results.tool_exec.success_count += 1
                        elif status == 'error':
                            self.results.tool_exec.error_count += 1
                        elif status == 'cancelled':
                            self.results.tool_exec.cancelled_count += 1

                        ts = event.get('ts', '')
                        if ts:
                            if first_ts is None:
                                first_ts = ts
                            last_ts = ts
                    except json.JSONDecodeError:
                        continue

            self.results.tool_exec.top_tools = dict(
                sorted(tool_counter.items(), key=lambda x: x[1], reverse=True)[:10]
            )
            self.results.tool_exec.first_event_ts = first_ts
            self.results.tool_exec.last_event_ts = last_ts

            logger.info(f"Tool exec summary: {self.results.tool_exec.total_events} events")

        except Exception as e:
            logger.warning(f"Failed to extract tool exec summary: {e}")
            self.results.tool_exec.errors.append(str(e))

    def _extract_evidence_summary(self, orch: Any) -> None:
        """Extract evidence log summary from real SQLite/JSONL file."""
        if not hasattr(orch, '_evidence_log') or not orch._evidence_log:
            logger.warning("No EvidenceLog found - skipping evidence summary")
            return

        try:
            # Get events from the live object
            try:
                events = orch._evidence_log.get_all_events()
                self.results.evidence.total_events = len(events)

                for event in events:
                    event_type = event.event_type if hasattr(event, 'event_type') else 'unknown'
                    self.results.evidence.event_types[event_type] = \
                        self.results.evidence.event_types.get(event_type, 0) + 1

                    if event_type == 'tool_call':
                        self.results.evidence.tool_call_count += 1
                    elif event_type == 'observation':
                        self.results.evidence.observation_count += 1
                    elif event_type == 'synthesis':
                        self.results.evidence.synthesis_count += 1
                    elif event_type == 'error':
                        self.results.evidence.error_count += 1
                    elif event_type == 'decision':
                        self.results.evidence.decision_count += 1
                    elif event_type == 'evidence_packet':
                        self.results.evidence.evidence_packet_count += 1

                if events:
                    first = events[0]
                    last = events[-1]
                    self.results.evidence.first_event_ts = (
                        first.timestamp.isoformat() if hasattr(first, 'timestamp') else str(first)
                    )
                    self.results.evidence.last_event_ts = (
                        last.timestamp.isoformat() if hasattr(last, 'timestamp') else str(last)
                    )
            except Exception as e:
                logger.warning(f"Could not get events from EvidenceLog: {e}")

            logger.info(f"Evidence summary: {self.results.evidence.total_events} events")

        except Exception as e:
            logger.warning(f"Failed to extract evidence summary: {e}")

    def _extract_metrics_summary(self, orch: Any) -> None:
        """Extract metrics registry summary from real JSONL file."""
        if not hasattr(orch, '_metrics_registry') or not orch._metrics_registry:
            logger.warning("No MetricsRegistry found - skipping metrics summary")
            return

        try:
            # Get summary from live object
            summary = orch._metrics_registry.get_summary()
            self.results.metrics.counter_count = summary.get('counter_count', 0)
            self.results.metrics.gauge_count = summary.get('gauge_count', 0)

            # Expected counters for this benchmark
            expected = [
                'ct_discovery_hits', 'ct_discovery_failures',
                'wayback_quick_hits', 'wayback_cdx_hits',
                'commoncrawl_hits', 'necromancer_rescues', 'necromancer_failures',
                'prf_expansions', 'onion_fetch_attempts',
                'mlx_cache_hits', 'mlx_cache_misses',
            ]

            found = []
            counters = summary.get('counters', {})
            for name in expected:
                if name in counters and counters[name] > 0:
                    found.append(name)

            self.results.metrics.expected_counters = expected
            self.results.metrics.found_counters = found
            self.results.metrics.missing_counters = [n for n in expected if n not in found]

            # Try to read from file for flush timestamps
            metrics_file = Path.home() / '.hledac' / 'runs' / 'logs' / 'metrics.jsonl'
            if metrics_file.exists():
                with open(metrics_file) as f:
                    lines = f.readlines()
                    self.results.metrics.total_samples = len(lines)
                    if lines:
                        try:
                            first = json.loads(lines[0])
                            last = json.loads(lines[-1])
                            self.results.metrics.first_flush_ts = first.get('ts', '')
                            self.results.metrics.last_flush_ts = last.get('ts', '')
                        except json.JSONDecodeError:
                            pass

            logger.info(f"Metrics summary: {self.results.metrics.counter_count} counters, "
                       f"{self.results.metrics.gauge_count} gauges")

        except Exception as e:
            logger.warning(f"Failed to extract metrics summary: {e}")

    def _extract_run_id(self, orch: Any) -> None:
        """Extract run_id from orchestrator for correlation."""
        if hasattr(orch, '_run_id') and orch._run_id:
            self.results.run_id = orch._run_id
        elif hasattr(orch, '_attr_run_id') and orch._attr_run_id:
            self.results.run_id = orch._attr_run_id

    def _compute_bottleneck_diagnosis(self) -> list[dict[str, Any]]:
        """Analyze results and identify top bottlenecks."""
        bottlenecks = []
        g = self.results.gating
        a = self.results.acquisition
        m = self.results.memory

        # Check gating bottleneck
        total_rejects = g.l0_rejects + g.l1_echo_rejects
        if total_rejects > g.admits * 10:
            bottlenecks.append({
                "location": "admission_gating",
                "evidence": f"{total_rejects} rejects vs {g.admits} admits",
                "impact": "high",
                "suggestion": "Review L0/L1 gate thresholds - too many candidates rejected"
            })

        # Check acquisition bottleneck
        total_acquisition_attempts = (
            a.ct_attempts + a.wayback_quick_attempts + a.wayback_cdx_attempts +
            a.commoncrawl_attempts + a.necromancer_attempts
        )
        if a.wayback_cdx_attempts > total_acquisition_attempts * 0.5:
            bottlenecks.append({
                "location": "wayback_cdx",
                "evidence": f"{a.wayback_cdx_attempts} CDX attempts ({a.wayback_cdx_lines} lines)",
                "impact": "medium",
                "suggestion": "Wayback CDX dominates acquisition latency - consider caching or early-stop tuning"
            })

        # Check memory pressure
        if m.memory_pressure_warn_count > 5:
            bottlenecks.append({
                "location": "memory_pressure",
                "evidence": f"{m.memory_pressure_warn_count} WARN events",
                "impact": "high",
                "suggestion": "Memory pressure频繁 - consider more aggressive cleanup or reduced concurrency"
            })

        # Check synthesis
        if self.results.synthesis.invoked and self.results.synthesis.latency_seconds > 30:
            bottlenecks.append({
                "location": "final_synthesis",
                "evidence": f"{self.results.synthesis.latency_seconds:.1f}s synthesis latency",
                "impact": "medium",
                "suggestion": "Synthesis is slow - consider context reduction or streaming"
            })

        # Check backlog churn
        if g.backlog_evictions + g.backlog_expiries > 50:
            bottlenecks.append({
                "location": "backlog_churn",
                "evidence": f"{g.backlog_evictions} evictions + {g.backlog_expiries} expiries",
                "impact": "medium",
                "suggestion": "High backlog churn - consider larger backlog or better eviction policy"
            })

        # Check phase distribution
        if len(self.results.phases) > 0:
            phase_durations = [(p.phase_name, p.duration_seconds) for p in self.results.phases]
            if phase_durations:
                max_phase = max(phase_durations, key=lambda x: x[1])
                if max_phase[1] > self.results.total_wall_clock_seconds * 0.7:
                    bottlenecks.append({
                        "location": f"phase_{max_phase[0]}",
                        "evidence": f"{max_phase[0]} took {max_phase[1]:.1f}s ({max_phase[1]/self.results.total_wall_clock_seconds*100:.0f}%)",
                        "impact": "high",
                        "suggestion": f"Phase {max_phase[0]} dominates runtime - investigate promotion criteria"
                    })

        return bottlenecks[:5]  # Top 5

    def _generate_report(self, bottlenecks: list[dict[str, Any]]) -> str:
        """Generate human-readable benchmark report."""
        r = self.results
        g = r.gating
        a = r.acquisition
        m = r.memory
        s = r.synthesis

        lines = [
            "=" * 70,
            "SPRINT 82J: E2E BENCHMARK RESULTS",
            "=" * 70,
            "",
            "## TIMING SUMMARY (Sprint 8B - Truthful)",
            f"  Total wall clock:      {r.total_wall_clock_seconds:.1f}s",
            f"  Research loop:         {r.research_loop_elapsed_s:.1f}s  # actual loop time",
            f"  Synthesis:             {r.synthesis_elapsed_s:.1f}s",
            f"  Teardown:             {r.teardown_elapsed_s:.1f}s",
            f"  Teardown substeps:   stop_collector={r.teardown_stop_collector_s:.3f}s cleanup={r.teardown_cleanup_s:.3f}s metrics={r.teardown_metrics_extract_s:.3f}s trace={r.teardown_trace_flush_s:.3f}s",
            f"  Live tasks at exit:   {r.post_loop_live_tasks_count}  {r.post_loop_live_task_names[:3]}",
            f"  Research runtime:      {r.research_runtime_seconds:.1f}s",
            f"  Time to first finding: {r.time_to_first_finding_seconds:.1f}s",
            f"  Final synthesis:       {r.final_synthesis_duration_seconds:.1f}s",
            f"  Echo rejection rate:   {r.echo_rejection_rate:.1%}  # l1_echo_rejects/(admits+rejects)",
            "",
            "## LOOP YIELD COUNTERS",
            f"  sleep0_count:         {r.sleep0_count}  # cooperative yields",
            f"  idle_sleep_count:     {r.idle_sleep_count}  # backoff sleeps",
            "",
            "## PER-ACTION ECHO (Sprint 8C)",
        ]

        # Sprint 8C: Per-action echo telemetry
        if r.action_echo_telemetry:
            for action, counts in sorted(r.action_echo_telemetry.items()):
                total = counts.get('admit', 0) + counts.get('hold', 0) + counts.get('reject', 0)
                if total > 0:
                    reject_rate = counts.get('reject', 0) / total
                    lines.append(f"  {action}: admit={counts.get('admit', 0)} hold={counts.get('hold', 0)} reject={counts.get('reject', 0)} ({reject_rate:.1%} reject)")
        else:
            lines.append("  (no telemetry)")
        lines.append("")

        lines.extend([
            "## PHASE BREAKDOWN",
        ])

        for p in r.phases:
            lines.append(f"  Phase {p.phase_name}: {p.duration_seconds:.1f}s")

        lines.extend([
            "",
            "## GATING / ADMISSION",
            f"  L0 rejects:           {g.l0_rejects}",
            f"  L1 echo rejects:      {g.l1_echo_rejects}",
            f"  L2 holds:            {g.l2_holds}",
            f"  Admits:               {g.admits}",
            f"  Backlog pushes:       {g.backlog_pushes}",
            f"  Backlog evictions:    {g.backlog_evictions}",
            f"  Deep read candidates: {g.deepening_gate_candidates}",
            "",
            "## ACQUISITION LAYERS",
            f"  CT discovery:          {a.ct_attempts} attempts, {a.ct_successes} success",
            f"  Wayback quick:         {a.wayback_quick_attempts} attempts",
            f"  Wayback CDX:           {a.wayback_cdx_attempts} attempts, {a.wayback_cdx_lines} lines",
            f"  Common Crawl:          {a.commoncrawl_attempts} attempts",
            f"  Necromancer:           {a.necromancer_attempts} attempts, {a.necromancer_rescues} rescues",
            f"  PRF expansions:        {a.prf_invocations} invocations",
            f"  Onion preflight:      {a.onion_preflight} checks",
            "",
            "## LANE ACTIVITY",
        ])

        for lane in r.lanes:
            lines.append(f"  {lane.role:20s}: {lane.actions:3d} actions, {lane.budget_spent:.1f} budget")

        lines.extend([
            "",
            "## MEMORY",
            f"  RSS start:            {m.rss_start_mb:.0f} MB",
            f"  RSS peak:             {m.rss_peak_mb:.0f} MB",
            f"  RSS before synthesis: {m.rss_before_synthesis_mb:.0f} MB",
            f"  RSS after synthesis:  {m.rss_after_synthesis_mb:.0f} MB",
            f"  Memory delta synth:   {m.memory_delta_synthesis_mb:.0f} MB",
            f"  Memory WARN events:   {m.memory_pressure_warn_count}",
            f"  Memory CRIT events:   {m.memory_pressure_critical_count}",
            f"  MLX cache clears:     {m.mlx_cache_clears}",
            "",
            "## SYNTHESIS",
            f"  Invoked:              {s.invoked}",
            f"  Fallback used:        {s.fallback_used}",
            f"  Context chars:        {s.final_context_chars}",
            f"  Context claims:       {s.final_context_claims}",
            f"  Knapsack selected:    {s.knapsack_selected}",
            f"  Knapsack dropped:     {s.knapsack_dropped}",
            f"  Latency:              {s.latency_seconds:.1f}s",
            f"  Winner-only evidence: {s.winner_only_evidence_count}",
            "",
            "## ITERATIONS",
            f"  Total iterations:     {r.iterations}",
            f"  Findings count:      {r.findings_count}",
            f"  Sources count:       {r.sources_count}",
            "",
            "## FPS METRICS (Sprint 7C)",
            f"  benchmark_fps:       {r.benchmark_fps:.1f} iter/s",
            f"  findings_fps:        {r.findings_fps:.1f} findings/s",
            f"  sources_fps:         {r.sources_fps:.1f} sources/s",
            f"  p95 latency:          {r.p95_latency_ms:.1f} ms",
            "",
            "## THERMAL",
            f"  Start state:         {r.thermal_state_start}",
            f"  Peak state:          {r.thermal_state_peak}",
            "",
            "## RUN CORRELATION",
            f"  Run ID:              {r.run_id or 'unknown'}",
            "",
            "## TOOL EXECUTION LOG (from file)",
            f"  Total events:        {r.tool_exec.total_events}",
            f"  Success:             {r.tool_exec.success_count}",
            f"  Errors:              {r.tool_exec.error_count}",
            f"  Chain valid:         {r.tool_exec.chain_valid}",
            f"  First event:        {r.tool_exec.first_event_ts or 'N/A'}",
            f"  Last event:         {r.tool_exec.last_event_ts or 'N/A'}",
            "  Top tools: " + (", ".join(f"{t}:{c}" for t, c in list(r.tool_exec.top_tools.items())[:5]) if r.tool_exec.top_tools else "N/A"),
            "",
            "## EVIDENCE LOG (from live object)",
            f"  Total events:        {r.evidence.total_events}",
            f"  Tool calls:         {r.evidence.tool_call_count}",
            f"  Observations:       {r.evidence.observation_count}",
            f"  Syntheses:          {r.evidence.synthesis_count}",
            f"  Decisions:           {r.evidence.decision_count}",
            f"  Evidence packets:   {r.evidence.evidence_packet_count}",
            f"  First event:        {r.evidence.first_event_ts or 'N/A'}",
            f"  Last event:         {r.evidence.last_event_ts or 'N/A'}",
            "",
            "## METRICS REGISTRY (from file)",
            f"  Counters:           {r.metrics.counter_count}",
            f"  Gauges:             {r.metrics.gauge_count}",
            f"  Total samples:      {r.metrics.total_samples}",
            f"  First flush:        {r.metrics.first_flush_ts or 'N/A'}",
            f"  Last flush:         {r.metrics.last_flush_ts or 'N/A'}",
        ])

        if r.metrics.missing_counters:
            lines.append(f"  Missing counters:   {', '.join(r.metrics.missing_counters)}")

        lines.extend([
            "",
            "=" * 70,
            "TOP BOTTLENECKS",
            "=" * 70,
        ])

        for i, bn in enumerate(bottlenecks, 1):
            lines.extend([
                "",
                f"{i}. {bn['location']}",
                f"   Evidence: {bn['evidence']}",
                f"   Impact:   {bn['impact']}",
                f"   Fix:      {bn['suggestion']}",
            ])

        lines.extend([
            "",
            "=" * 70,
        ])

        return "\n".join(lines)

    async def run(self) -> BenchmarkResults:
        """Run the E2E benchmark."""
        logger.info(f"Starting E2E benchmark: {self.config.duration_seconds}s run")
        self._start_time = time.time()

        # Record start state
        self.results.memory.rss_start_mb = self._get_rss_mb()
        self.results.memory.rss_peak_mb = self.results.memory.rss_start_mb

        try:
            # Setup orchestrator
            logger.info("Initializing orchestrator...")
            orch = await self._setup_orchestrator()
            # Sprint 8F: Store orch reference for teardown (avoids 'orch' unbound in finally)
            self._orch = orch

            # Record RSS after initialization
            init_rss = self._get_rss_mb()
            self.results.memory.rss_peak_mb = max(self.results.memory.rss_peak_mb, init_rss)
            logger.info(f"Orchestrator initialized, RSS: {init_rss:.0f}MB")

            # Track memory and metrics in background
            async def track_metrics():
                """Background metrics tracking during benchmark run."""
                iteration_count = 0
                phase_history = []
                thermal_history = []
                # Sprint 7E: Checkpoint tracking
                last_checkpoint_time = time.time()
                last_checkpoint_iter = 0

                while True:
                    rss = self._get_rss_mb()
                    self.results.memory.rss_peak_mb = max(self.results.memory.rss_peak_mb, rss)
                    self._rss_samples.append(rss)

                    # Track thermal state
                    try:
                        if hasattr(orch, '_memory_mgr') and orch._memory_mgr:
                            thermal = orch._memory_mgr.get_thermal_state()
                            thermal_name = thermal.name.lower() if hasattr(thermal, 'name') else str(thermal)
                            if not thermal_history or thermal_history[-1] != thermal_name:
                                thermal_history.append(thermal_name)
                            # Update peak thermal
                            thermal_order = {'normal': 0, 'warm': 1, 'hot': 2, 'critical': 3}
                            current_level = thermal_order.get(thermal_name, 0)
                            peak_level = thermal_order.get(self.results.thermal_state_peak, 0)
                            if current_level > peak_level:
                                self.results.thermal_state_peak = thermal_name
                    except Exception:
                        pass  # Thermal tracking is best-effort

                    # Sample sprint state metrics during run
                    if hasattr(orch, '_sprint_state'):
                        state = orch._sprint_state
                        # Track iteration progress
                        current_iter = state.get('_iter_count', iteration_count)
                        if current_iter > iteration_count:
                            iteration_count = current_iter
                            # Track time to first finding
                            if self._first_finding_time is None and len(state.get('confirmed', [])) > 0:
                                self._first_finding_time = time.time() - research_start

                        # Track phase changes
                        if hasattr(orch, '_phase_controller'):
                            pc = orch._phase_controller
                            current_phase = str(pc.current_phase.name) if hasattr(pc, 'current_phase') else "unknown"
                            if not phase_history or phase_history[-1] != current_phase:
                                phase_history.append(current_phase)
                                now = time.time()
                                # Record phase timing
                                if len(self.results.phases) == 0 or self.results.phases[-1].phase_name != current_phase:
                                    self.results.phases.append(BenchmarkPhaseMetrics(
                                        phase_name=current_phase,
                                        enter_time=now - research_start,
                                    ))

                    # Sprint 7E: Periodic checkpoint writing
                    now = time.time()
                    elapsed_since_checkpoint = now - last_checkpoint_time
                    current_iterations = iteration_count
                    if (self.config.jsonl_path and
                        elapsed_since_checkpoint >= CHECKPOINT_INTERVAL_S and
                        current_iterations > last_checkpoint_iter):
                        # Write checkpoint without blocking
                        self._write_checkpoint(orch, now - self._start_time, self.config.jsonl_path)
                        last_checkpoint_time = now
                        last_checkpoint_iter = current_iterations

                    await asyncio.sleep(5)  # Sample every 5 seconds

            metrics_task = asyncio.create_task(track_metrics())

            # Run research with timeout
            logger.info(f"Running research for {self.config.duration_seconds}s...")
            research_start = time.time()

            # Sprint 82N: Mark that research() was entered
            self.results.research_entered = True

            # Sprint 8H: Capture config values before wait_for to avoid any descriptor shadowing
            cfg_timeout = self.config.duration_seconds
            cfg_query = self.config.query
            cfg_mode = self.config.mode

            try:
                result = await asyncio.wait_for(
                    orch.research(
                        query=cfg_query,
                        timeout=cfg_timeout,
                        offline_replay=(cfg_mode == "OFFLINE_REPLAY"),
                    ),
                    timeout=float(cfg_timeout) + 60.0
                )
            except TimeoutError:
                logger.warning("Research timed out - collecting partial results")
                result = None

            research_end = time.time()
            self.results.research_runtime_seconds = research_end - research_start

            # Sprint 8B: Extract research_loop_elapsed from orchestrator (actual loop-only time)
            self.results.research_loop_elapsed_s = getattr(orch, '_research_loop_elapsed_s', self.results.research_runtime_seconds)
            self.results.synthesis_elapsed_s = getattr(orch, '_synthesis_elapsed_s', 0.0)

            # Stop memory tracking
            stop_collector_start = time.perf_counter()
            metrics_task.cancel()
            try:
                await metrics_task
            except asyncio.CancelledError:
                pass
            self.results.teardown_stop_collector_s = time.perf_counter() - stop_collector_start
            logger.info(f"[TEARDOWN DIAG] after_stop_collector: {(time.perf_counter()-stop_collector_start)*1000:.1f}ms")

            # Sprint 8F: Task diagnostics - snapshot live tasks at teardown boundary
            try:
                loop = asyncio.get_running_loop()
                all_tasks = asyncio.all_tasks(loop)
                current = asyncio.current_task(loop)
                other_tasks = [t for t in all_tasks if t is not current]
                self.results.post_loop_live_tasks_count = len(other_tasks)
                self.results.post_loop_live_task_names = [
                    t.get_name() if hasattr(t, 'get_name') else repr(t)
                    for t in other_tasks
                ]
                logger.info(f"[TEARDOWN] {len(other_tasks)} live tasks: {self.results.post_loop_live_task_names}")
            except Exception as e:
                logger.warning(f"Task diagnostics failed: {e}")

            # Sprint 8H: Call orch.cleanup() to cancel background tasks and reduce orphaned task count
            cleanup_start = time.perf_counter()
            if hasattr(orch, 'cleanup'):
                try:
                    await orch.cleanup()
                except Exception as e:
                    logger.warning(f"Orchestrator cleanup failed: {e}")
            self.results.teardown_cleanup_s = time.perf_counter() - cleanup_start

            # Sprint 8H: Measure remaining live tasks after cleanup
            try:
                loop = asyncio.get_running_loop()
                all_tasks = asyncio.all_tasks(loop)
                current = asyncio.current_task(loop)
                remaining_tasks = [t for t in all_tasks if t is not current]
                logger.info(f"[TEARDOWN] After cleanup: {len(remaining_tasks)} remaining tasks")
            except Exception:
                pass

            # Record RSS before synthesis
            self.results.memory.rss_before_synthesis_mb = self._get_rss_mb()

            # Sprint 82Q Phase 2: Extract trace metrics BEFORE flush (while buffer still in memory)
            metrics_extract_start = time.perf_counter()
            self._extract_trace_metrics(orch)

            # Sprint 82Q Phase 2: Flush trace to disk after extraction
            trace_flush_start = time.perf_counter()
            if hasattr(orch, '_flush_iteration_trace'):
                try:
                    orch._flush_iteration_trace()
                except Exception as e:
                    logger.warning(f"Trace flush failed: {e}")
            self.results.teardown_trace_flush_s = time.perf_counter() - trace_flush_start

            # Sprint 8F: Metrics extract substep timing
            metrics_extract_start = time.perf_counter()
            self._extract_sprint_state_metrics(orch)
            self._extract_phase_metrics(orch)
            self._extract_lane_metrics(orch)
            self.results.teardown_metrics_extract_s = time.perf_counter() - metrics_extract_start

            # Sprint 82K: Extract from real log/metrics files
            self._extract_run_id(orch)
            self._extract_tool_exec_summary(orch)
            self._extract_evidence_summary(orch)
            self._extract_metrics_summary(orch)

            # Populate from result if available
            if result:
                self.results.findings_count = len(result.findings) if result.findings else 0
                self.results.sources_count = result.total_sources_checked if hasattr(result, 'total_sources_checked') else 0
                self.results.iterations = result.statistics.get('iterations', 0) if result.statistics else 0

            # Sprint 7C: Extract FPS metriky a p95 latency z orchestratoru
            if hasattr(orch, '_benchmark_metrics_cache'):
                metrics = orch._benchmark_metrics_cache
                self.results.benchmark_fps = metrics.get('benchmark_fps', 0.0)
                self.results.findings_fps = metrics.get('findings_fps', 0.0)
                self.results.sources_fps = metrics.get('sources_fps', 0.0)
                self.results.p95_latency_ms = metrics.get('p95_latency_ms', 0.0)

            # Extract synthesis metrics if available
            if hasattr(orch, '_sprint_state'):
                state = orch._sprint_state
                self.results.synthesis.invoked = True
                self.results.synthesis.final_context_chars = state.get('final_context_chars', 0)
                self.results.synthesis.final_context_claims = len(state.get('confirmed', [])) + len(state.get('falsified', []))
                self.results.synthesis.claims_emitted = self.results.synthesis.final_context_claims

            # Record RSS after synthesis
            self.results.memory.rss_after_synthesis_mb = self._get_rss_mb()
            self.results.memory.memory_delta_synthesis_mb = (
                self.results.memory.rss_after_synthesis_mb - self.results.memory.rss_before_synthesis_mb
            )

            # Calculate final synthesis duration
            self.results.final_synthesis_duration_seconds = getattr(
                result, 'execution_time', 0
            ) * 0.3 if result else 0  # Approximate

        except Exception as e:
            logger.error(f"Benchmark error: {e}", exc_info=True)

        finally:
            # Sprint 8B: Calculate total time AFTER all work is done
            total_end = time.time()
            elapsed_s = total_end - self._start_time
            self.results.total_wall_clock_seconds = elapsed_s

            # Sprint 8B: Separate teardown time from synthesis
            # synthesis_elapsed is already measured during synthesis phase
            # teardown = total - research_loop - synthesis
            self.results.teardown_elapsed_s = max(
                0.0,
                elapsed_s - self.results.research_loop_elapsed_s - self.results.synthesis_elapsed_s
            )

            # Sprint 8B: Calculate FPS using RESEARCH LOOP time (not wall clock)
            # This is the truthful throughput measure
            loop_time = self.results.research_loop_elapsed_s
            if loop_time > 0:
                self.results.benchmark_fps = self.results.iterations / loop_time
                self.results.findings_fps = self.results.findings_count / loop_time
                self.results.sources_fps = self.results.sources_count / loop_time
            elif elapsed_s > 0:
                # Fallback: use wall clock if no loop time
                self.results.benchmark_fps = self.results.iterations / elapsed_s
                self.results.findings_fps = self.results.findings_count / elapsed_s
                self.results.sources_fps = self.results.sources_count / elapsed_s

            # Generate report
            report_gen_start = time.perf_counter()
            bottlenecks = self._compute_bottleneck_diagnosis()
            report = self._generate_report(bottlenecks)
            logger.info(f"[TEARDOWN DIAG] report_gen: {time.perf_counter()-report_gen_start:.3f}s")

            # Save results (async to not block event loop)
            self.config.output_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = self.config.output_dir / f"benchmark_{timestamp}.txt"
            json_path = self.config.output_dir / f"benchmark_{timestamp}.json"

            # Sprint 8H: Write files async to avoid blocking event loop
            report_write_start = time.perf_counter()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: report_path.write_text(report))
            self.results.teardown_report_write_s = time.perf_counter() - report_write_start
            logger.info(f"[TEARDOWN DIAG] report_write: {self.results.teardown_report_write_s:.3f}s")

            # Sprint 8B: Compute echo rejection rate
            admits = self.results.gating.admits
            echo_rejects = self.results.gating.l1_echo_rejects
            total_echo = admits + echo_rejects
            self.results.echo_rejection_rate = echo_rejects / total_echo if total_echo > 0 else 0.0

            # Sprint 8H: Wire additional truth fields
            self.results.hh_index = self.results.action_selection_hhi
            self.results.total_wall_clock_s = self.results.total_wall_clock_seconds

            # Sprint 8H: Wire data_mode from orchestrator (set in research() based on offline_replay param)
            if hasattr(self, '_orch') and hasattr(self._orch, '_data_mode'):
                self.results.data_mode = self._orch._data_mode

            # Sprint 8B/8C: Yield counters extracted in _extract_sprint_state_metrics (receives orch directly)

            # Convert results to dict (handle non-serializable fields)
            results_dict = {
                'total_wall_clock_seconds': self.results.total_wall_clock_seconds,
                'research_runtime_seconds': self.results.research_runtime_seconds,
                # Sprint 8B: Truthful timing breakdown
                'research_loop_elapsed_s': self.results.research_loop_elapsed_s,
                'synthesis_elapsed_s': self.results.synthesis_elapsed_s,
                'teardown_elapsed_s': self.results.teardown_elapsed_s,
                # Sprint 8F: Teardown substeps
                'teardown_stop_collector_s': self.results.teardown_stop_collector_s,
                'teardown_cleanup_s': self.results.teardown_cleanup_s,
                'teardown_metrics_extract_s': self.results.teardown_metrics_extract_s,
                'teardown_trace_flush_s': self.results.teardown_trace_flush_s,
                'time_to_first_finding_seconds': self.results.time_to_first_finding_seconds,
                'time_to_first_high_confidence_seconds': self.results.time_to_first_high_confidence_seconds,
                'time_to_first_deep_read_seconds': self.results.time_to_first_deep_read_seconds,
                'final_synthesis_duration_seconds': self.results.final_synthesis_duration_seconds,
                'iterations': self.results.iterations,
                'findings_count': self.results.findings_count,
                'sources_count': self.results.sources_count,
                # Sprint 8B: Yield counters
                'sleep0_count': self.results.sleep0_count,
                'idle_sleep_count': self.results.idle_sleep_count,
                # Sprint 8B: Echo rejection rate
                'echo_rejection_rate': self.results.echo_rejection_rate,
                'gating': self.results.gating.__dict__,
                'acquisition': self.results.acquisition.__dict__,
                'memory': self.results.memory.__dict__,
                'synthesis': self.results.synthesis.__dict__,
                'lanes': [l.__dict__ for l in self.results.lanes],
                'bottlenecks': bottlenecks,
                # Sprint 86: Network Recon Economics
                'network_recon_precondition_met_count': self.results.network_recon_precondition_met_count,
                'network_recon_precondition_met_but_not_selected_count': self.results.network_recon_precondition_met_but_not_selected_count,
                'network_recon_selected_count': self.results.network_recon_selected_count,
                'network_recon_executed_count': self.results.network_recon_executed_count,
                'network_recon_success_count': self.results.network_recon_success_count,
                'network_recon_partial_success_count': self.results.network_recon_partial_success_count,
                'network_recon_empty_count': self.results.network_recon_empty_count,
                'network_recon_candidates_generated': self.results.network_recon_candidates_generated,
                'network_recon_candidates_forwarded': self.results.network_recon_candidates_forwarded,
                'network_recon_candidates_dropped_queue_full': self.results.network_recon_candidates_dropped_queue_full,
                # Sprint 86E-R2: Precondition breakdown
                'network_recon_precondition_rejected_scanned': self.results.network_recon_precondition_rejected_scanned,
                'network_recon_precondition_rejected_budget': self.results.network_recon_precondition_rejected_budget,
                'network_recon_queue_had_items_at_score_time': self.results.network_recon_queue_had_items_at_score_time,
                'network_recon_queue_empty_at_score_time': self.results.network_recon_queue_empty_at_score_time,
                'network_recon_queue_size_avg': self.results.network_recon_queue_size_avg,
                'network_recon_yield_ratio': self.results.network_recon_yield_ratio,
                'network_recon_forwarding_efficiency': self.results.network_recon_forwarding_efficiency,
                'network_recon_selection_rate_pct': self.results.network_recon_selection_rate_pct,

                # Sprint 86F: Wildcard metrics
                'network_recon_wildcard_hit_count': self.results.network_recon_wildcard_hit_count,
                'network_recon_wildcard_miss_count': self.results.network_recon_wildcard_miss_count,
                'network_recon_wildcard_hit_rate': self.results.network_recon_wildcard_hit_rate,
                'network_recon_subdomains_found_before_gate_total': self.results.network_recon_subdomains_found_before_gate_total,
                'network_recon_subdomains_suppressed_by_wildcard_total': self.results.network_recon_subdomains_suppressed_by_wildcard_total,
                'network_recon_wildcard_but_has_mx_ns_txt_findings_count': self.results.network_recon_wildcard_but_has_mx_ns_txt_findings_count,

                # Sprint 86F: Score history percentiles
                'network_recon_score_p50': self.results.network_recon_score_p50,
                'network_recon_score_p90': self.results.network_recon_score_p90,

                'action_selection_hhi': self.results.action_selection_hhi,
                'action_selection_counts': self.results.action_selection_counts,
                # Sprint 8B: Per-action latency stats from orchestrator
                'action_latency_stats': getattr(self._orch, '_action_latency_stats', {}) if hasattr(self, '_orch') else {},
                # Sprint 8H: data_mode must be present in JSON output
                'data_mode': getattr(self._orch, '_data_mode', 'SYNTHETIC_MOCK') if hasattr(self, '_orch') else 'SYNTHETIC_MOCK',
                # Sprint 8H: Additional truth fields
                'hh_index': self.results.hh_index,
                'total_wall_clock_s': self.results.total_wall_clock_s,
                'post_loop_live_tasks_count': self.results.post_loop_live_tasks_count,
                'post_loop_live_task_names': self.results.post_loop_live_task_names,
            }

            # Sprint 8H: Write JSON async to avoid blocking event loop
            json_write_start = time.perf_counter()
            await asyncio.to_thread(json_path.write_text, json.dumps(results_dict, indent=2))
            self.results.teardown_summary_write_s = time.perf_counter() - json_write_start

            # Sprint 7E: Write final JSONL checkpoint and summary
            if self.config.jsonl_path:
                # Write final checkpoint
                try:
                    checkpoint = {
                        "elapsed_s": elapsed_s,
                        "iterations": self.results.iterations,
                        "benchmark_fps": self.results.benchmark_fps,
                        "findings_total": self.results.findings_count,
                        "findings_fps": self.results.findings_fps,
                        "sources_total": self.results.sources_count,
                        "sources_fps": self.results.sources_fps,
                        "HHI": self.results.action_selection_hhi,
                        "p95_latency_ms": self.results.p95_latency_ms,
                        "state_cache_hit_rate": getattr(self._orch, '_state_cache_hit_rate', 0.0) if hasattr(self, '_orch') else 0.0,
                        "data_mode": getattr(self._orch, '_data_mode', 'SYNTHETIC_MOCK'),
                    }

                    def _write_final():
                        with open(self.config.jsonl_path, "a") as f:
                            f.write(json.dumps(checkpoint) + "\n")
                    asyncio.get_event_loop().run_in_executor(None, _write_final)
                except Exception as e:
                    logger.warning(f"Failed to write final checkpoint: {e}")

            if self.config.summary_path:
                # Write summary JSON
                summary = {
                    "stop_reason": "completed",
                    "elapsed_s": elapsed_s,
                    "iterations": self.results.iterations,
                    "benchmark_fps": self.results.benchmark_fps,
                    "findings_total": self.results.findings_count,
                    "findings_fps": self.results.findings_fps,
                    "sources_total": self.results.sources_count,
                    "sources_fps": self.results.sources_fps,
                    "HHI": self.results.action_selection_hhi,
                    "p95_latency_ms": self.results.p95_latency_ms,
                    "state_cache_hit_rate": getattr(self._orch, '_state_cache_hit_rate', 0.0) if hasattr(self, '_orch') else 0.0,
                    "offline_guard_complete": True,
                    "benchmark_valid": self.results.iterations > 0,
                }

                def _write_summary():
                    with open(self.config.summary_path, "w") as f:
                        f.write(json.dumps(summary, indent=2))
                asyncio.get_event_loop().run_in_executor(None, _write_summary)

            print(report)
            print(f"\nResults saved to: {report_path}")
            print(f"JSON data saved to: {json_path}")

        return self.results


async def run_benchmark(
    duration_seconds: int = BENCHMARK_FULL_SECONDS,
    query: str = "artificial intelligence future trends 2025",
    output_dir: str = "./benchmark_results",
    verbose: bool = False,
    mode: str = "SYNTHETIC_MOCK",
    silent: bool = False,
    jsonl_path: str = "",
    summary_path: str = "",
) -> BenchmarkResults:
    """
    Run E2E benchmark with specified parameters.

    Args:
        duration_seconds: How long to run the benchmark (120 for smoke, 600 for full)
        query: Research query to use
        output_dir: Where to save results
        verbose: Enable verbose logging
        mode: Benchmark mode - SYNTHETIC_MOCK or OFFLINE_REPLAY
        silent: Suppress stdout, write truth data to files only
        jsonl_path: Path for JSONL checkpoint output
        summary_path: Path for JSON summary output

    Returns:
        BenchmarkResults with all collected metrics
    """
    config = BenchmarkConfig(
        duration_seconds=duration_seconds,
        query=query,
        output_dir=Path(output_dir),
        verbose=verbose,
        mode=mode,
        silent=silent,
        jsonl_path=jsonl_path,
        summary_path=summary_path,
    )

    benchmark = E2EBenchmark(config)
    return await benchmark.run()


def main():
    """CLI entry point for benchmark."""
    import argparse

    parser = argparse.ArgumentParser(description="Sprint 82J E2E Benchmark")
    parser.add_argument(
        "--duration",
        type=int,
        default=BENCHMARK_FULL_SECONDS,
        help=f"Benchmark duration in seconds (default: {BENCHMARK_FULL_SECONDS}, use 120 for smoke)",
    )
    parser.add_argument(
        "--query",
        type=str,
        default="artificial intelligence future trends 2025",
        help="Research query",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="./benchmark_results",
        help="Output directory",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    # Sprint 7E: Silent benchmark harness arguments
    parser.add_argument(
        "--mode",
        type=str,
        default="SYNTHETIC_MOCK",
        choices=["SYNTHETIC_MOCK", "OFFLINE_REPLAY"],
        help="Benchmark mode",
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Suppress stdout, write truth data to files only",
    )
    parser.add_argument(
        "--jsonl",
        type=str,
        default="",
        help="Path for JSONL checkpoint output",
    )
    parser.add_argument(
        "--summary",
        type=str,
        default="",
        help="Path for JSON summary output",
    )

    args = parser.parse_args()

    # Sprint 7F: DO NOT set HLEDAC_OFFLINE=1 - that blocks ALL handlers.
    # OFFLINE_REPLAY uses replay-backed handlers, not live network. The mode
    # flag is passed directly to orchestrator.research(offline_replay=True).

    # Sprint 7E: Suppress stdout in silent mode
    import sys
    if args.silent:
        # Redirect stdout/stderr to suppress output
        sys.stdout = open(sys.stdout.fileno(), 'w', buffering=1) if sys.stdout.isatty() else sys.stdout
        sys.stderr = open(sys.stderr.fileno(), 'w', buffering=1) if sys.stderr.isatty() else sys.stderr

    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else (logging.WARNING if args.silent else logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Sprint 7E: Determine output paths
    jsonl_path = Path(args.jsonl) if args.jsonl else Path(BENCHMARK_LOG_PATH)
    summary_path = Path(args.summary) if args.summary else Path(BENCHMARK_SUMMARY_PATH)

    # Run benchmark
    asyncio.run(run_benchmark(
        duration_seconds=args.duration,
        query=args.query,
        output_dir=args.output,
        verbose=args.verbose,
        mode=args.mode,
        silent=args.silent,
        jsonl_path=str(jsonl_path),
        summary_path=str(summary_path),
    ))


if __name__ == "__main__":
    main()
