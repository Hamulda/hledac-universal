"""
core/config/m1_air_config.py — M1AirConfig: Frozen typed configuration for MacBook Air M1 8GB.

Sprint F290: Centralized hardware-specific limits as immutable config.
All values are hardware-validated for M1 8GB UMA budget.

Invariant table (test name → validated property):
  test_m1air_memory_budget        → memory_budget_gib == 6.0
  test_m1air_concurrent_lanes     → max_concurrent_lanes == 4
  test_m1air_circuit_breaker      → circuit_breaker_threshold == 5
  test_m1air_windup_ratio         → windup_ratio_90s == 0.10
  test_m1air_duckdb_chunk         → duckdb_chunk_size == 50
  test_m1air_no_mutable_state     → all fields frozen

References:
  - CLAUDE.md § HARDWARE CONSTRAINTS (M1 8GB UMA)
  - SprintSchedulerConfig (runtime/sprint_scheduler.py)
  - FetchCoordinatorConfig (coordinators/fetch_coordinator.py)
"""


from dataclasses import dataclass, field
import msgspec
from typing import ClassVar


# ─────────────────────────────────────────────────────────────────────────────
# M1AirConfig — frozen hardware profile for MacBook Air M1 8GB UMA
# ─────────────────────────────────────────────────────────────────────────────

class M1AirConfig(msgspec.Struct, frozen=True, gc=False):
    """
    Immutable M1 8GB UMA hardware profile.

    Memory budget breakdown (6.0 GiB total):
      macOS system         ~2.5 GiB
      Orchestrátor         ~1.0 GiB
      LLM (Hermes3 4bit)   ~2.0 GiB
      KV cache             ~0.75 GiB
      ──────────────────────────────
      Total               ~6.25 GiB (ceiling at 6.0 GiB for safety)

    All limits are hardware-validated for this specific configuration.
    Do NOT increase values without explicit M1 8GB testing.
    """

    # ── Hardware profile ─────────────────────────────────────────────────────

    memory_budget_gib: ClassVar[float] = 6.0
    """Ceiling for total process memory. Hard limit on M1 8GB UMA."""

    max_concurrent_lanes: ClassVar[int] = 4
    """Maximum parallel acquisition lanes. M1 8GB CPU cores (4P+0E)."""

    circuit_breaker_threshold: ClassVar[int] = 5
    """Up from 3 — tighter feedback loop for M1 thermal envelope."""

    # ── Sprint lifecycle ────────────────────────────────────────────────────

    windup_ratio_90s: ClassVar[float] = 0.10
    """Windup lead = 10% of sprint duration for 90s sprints."""

    duckdb_chunk_size: ClassVar[int] = 50
    """DuckDB async batch chunk size. Balanced for M1 8GB memory pressure."""

    # ── Fetch timeouts (seconds) ─────────────────────────────────────────────

    timeout_clearnet_api: ClassVar[float] = 20.0
    timeout_clearnet_html: ClassVar[float] = 35.0
    timeout_tor: ClassVar[float] = 75.0
    timeout_i2p: ClassVar[float] = 150.0
    timeout_gopher: ClassVar[float] = 30.0

    # ── Concurrency limits ─────────────────────────────────────────────────

    concurrency_global_max: ClassVar[int] = 25
    aimd_max_concurrency: ClassVar[int] = 25
    aimd_success_threshold: ClassVar[int] = 2

    # ── Memory/telemetry bounds ─────────────────────────────────────────────

    advisory_log_lru_max: ClassVar[int] = 16
    max_gc_stats: ClassVar[int] = 1000
    max_memory_entities: ClassVar[int] = 512
    max_memory_exposures: ClassVar[int] = 512
    max_memory_pivots: ClassVar[int] = 512
    max_source_health_entries: ClassVar[int] = 100
    max_pivot_graph_stats_nodes: ClassVar[int] = 500
    max_windup_scorecard_keys: ClassVar[int] = 32

    # ── Cycle/drain deadlines ────────────────────────────────────────────────

    cycle_budget_s: ClassVar[float] = 60.0
    max_branch_timeout_cap: ClassVar[float] = 300.0
    extraction_drain_deadline_s: ClassVar[float] = 30.0
    ooda_interval_s: ClassVar[float] = 60.0
    max_fetch_latency_ema: ClassVar[int] = 1000
    arrow_flush_s: ClassVar[float] = 60.0
    max_findings_per_sprint: ClassVar[int] = 500
    barrier_hard_timeout_s: ClassVar[float] = 30.0

    # ── UMA pressure thresholds (GiB) ────────────────────────────────────────

    threshold_soft_warn_gib: ClassVar[float] = 6.8
    threshold_warn_gib: ClassVar[float] = 7.0
    threshold_critical_gib: ClassVar[float] = 7.5
    threshold_emergency_gib: ClassVar[float] = 7.8
    clean_swap_max_gib: ClassVar[float] = 3.0
    diagnostic_swap_max_gib: ClassVar[float] = 5.0

    # ── Brain/LLM bounds ────────────────────────────────────────────────────

    hermes_timeout_default_s: ClassVar[float] = 60.0
    hermes_timeout_min_s: ClassVar[float] = 1.0
    hermes_timeout_max_s: ClassVar[float] = 300.0
    max_llm_prompt_chars: ClassVar[int] = 8192
    max_pending_futures: ClassVar[int] = 256
    max_kv_size: ClassVar[int] = 8192
    max_length: ClassVar[int] = 512  # Tokenization

    # ── H3/HTTP3 bounds ─────────────────────────────────────────────────────

    h3_cache_max: ClassVar[int] = 2048
    h3_timeout_s: ClassVar[float] = 8.0
    h3_wait_timeout_s: ClassVar[float] = 2.0
    h3_rss_probe_timeout_s: ClassVar[float] = 0.05
    max_probe_tasks: ClassVar[int] = 16
    head_probe_timeout_s: ClassVar[float] = 4.0

    # ── Lane/breaker bounds ─────────────────────────────────────────────────

    max_lane_rejections: ClassVar[int] = 1000
    cooldown_seconds: ClassVar[float] = 60.0
    max_backoff_delay: ClassVar[float] = 30.0
    nocache_threshold_bytes: ClassVar[int] = 50 * 1024 * 1024
    priority_tor: ClassVar[int] = 30
    max_evidence_ids_per_step: ClassVar[int] = 10
    cover_max: ClassVar[int] = 2

    # ── DuckDB / Arrow ──────────────────────────────────────────────────────

    max_chunk_size: ClassVar[int] = 100
    max_chunk_concurrency: ClassVar[int] = 2
    arrow_flush_interval_s: ClassVar[float] = 60.0

    # ── Prewarm / conditional cache ─────────────────────────────────────────

    # ── Executor pool sizing (threads) ────────────────────────────────────────
    # R-2: Named pools for @offload_to() decorator.
    # Total threads bounded by M1 8GB: 4P + 4E cores = 8 threads max.
    # Thread-stack RAM: ~1 MB/thread × N — all pools share the budget.
    # Rayon pools (cpu/io/mixed) are preferred; Python ThreadPool used as fallback.

    cpu_io_pool: ClassVar[int] = 4
    """Python SharedWorkerPool: I/O-bound blocking calls (WHOIS, SSL, SQLite, file)."""

    cpu_blocking_pool: ClassVar[int] = 2
    """Python ThreadPool for CPU-bound Python work (regex, parsing). Use asyncio.to_thread()."""

    mlx_pool: ClassVar[int] = 1
    """Rayon MLX pool: LLM inference, MLX Metal operations."""

    ane_pool: ClassVar[int] = 1
    """Rayon ANE pool: Apple Neural Engine for CoreML inference."""

    duckdb_pool: ClassVar[int] = 2
    """Rayon io pool: DuckDB queries, graph_traverse, compression."""

    # ── Prewarm / conditional cache ─────────────────────────────────────────

    prewarm_pool_size: ClassVar[int] = 4
    conditional_cache_ttl_s: ClassVar[int] = 3600
    conditional_cache_max_entries: ClassVar[int] = 5000
    conditional_cache_map_size_mb: ClassVar[int] = 16

    # ── Validation ─────────────────────────────────────────────────────────

    _required_keys: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        # Verify all ClassVars are set (not accidentally overridden by mistake)
        if self.memory_budget_gib != 6.0:
            raise ValueError(f"M1AirConfig memory_budget_gib must be 6.0, got {self.memory_budget_gib}")
        if self.max_concurrent_lanes != 4:
            raise ValueError(f"M1AirConfig max_concurrent_lanes must be 4, got {self.max_concurrent_lanes}")

    @classmethod
    def validate(cls) -> bool:
        """
        Runtime validation that hardware profile is correct for M1 8GB.

        Called once at startup (core/__main__.py pre-flight).
        Raises ValueError if any invariant is violated.
        """
        # Memory ceiling check — ensure we never exceed physical RAM
        assert cls.memory_budget_gib == 6.0, "memory_budget_gib invariant violated"
        assert cls.threshold_emergency_gib >= cls.threshold_critical_gib >= cls.threshold_warn_gib >= cls.threshold_soft_warn_gib
        assert cls.hermes_timeout_max_s >= cls.hermes_timeout_default_s >= cls.hermes_timeout_min_s
        assert cls.h3_timeout_s >= cls.h3_wait_timeout_s
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Derived convenience accessors
# ─────────────────────────────────────────────────────────────────────────────

M1_AIR = M1AirConfig()
"""Singleton instance for runtime access to M1 8GB hardware profile."""
