"""
config/settings.py — Centralized Settings for Hledac Universal

Architecture (Issue #44):












    Single Settings class with msgspec.Struct + lazy ENV resolution.
    Bound settings imported once at startup.

Design decisions:
    1. msgspec.Struct: zero-copy, M1 8GB friendly, Python 3.14+ compatible
    2. Lazy env resolution: values computed on first access, cached in ENV.lru_cache
    3. Domain organization: FetchSettings, MLXSettings, DuckDBSettings,
       DedupSettings, TransportSettings, SecuritySettings, etc.
    4. Backward compat: ENV singleton still available for raw/dynamic lookups

msgspec.Struct vs pydantic:
    - msgspec: 10-100× faster serialization, zero-copy decode, no Schema validation overhead
    - pydantic: richer validation, more deps, slower
    - Decision: msgspec.Struct (project uses it everywhere, lighter weight for M1)

Invariant tests (TestSprint44):
    INV: settings_singleton — only one Settings instance
    INV: settings_lazy — no env lookups at import time
    INV: settings_bounded — all collections have explicit max sizes
    INV: settings_fail_safe — Exception → default returned
    INV: settings_mlx_cache_hermes — mlx settings use correct kv_bits/max_kv_size
    INV: settings_duckdb_threads — duckdb threads capped at 4 for M1
    INV: settings_metal_cache — metal cache bounded to [512MiB, 1GiB]
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import msgspec

from hledac.universal.core.env_config import ENV

__all__ = [
    "Settings",
    "settings",
    # Domain structs
    "FetchSettings",
    "MLXSettings",
    "DuckDBSettings",
    "DedupSettings",
    "TransportSettings",
    "MemorySettings",
    "SprintSettings",
    "GraphSettings",
    "SynthesisSettings",
    "CooldownSettings",
]


# ---------------------------------------------------------------------------
# Domain Structs (msgspec.Struct — immutable, zero-copy)
# ---------------------------------------------------------------------------


class FetchSettings(msgspec.Struct, frozen=True, gc=False):
    """HTTP fetching configuration."""

    # curl_cffi / JA3 fingerprinting
    curl_cffi_pool_size: int = 4
    curl_cffi_prewarm: bool = True
    curl_cffi_conditional_cache: bool = True

    # HTTP/3 (QUIC)
    http3_enabled: bool = True  # HLEDAC_ENABLE_HTTPX_H3 (default ON)
    http3_cache_max: int = 1024
    http3_concurrency_max: int = 3
    http3_timeout_s: float = 8.0
    http3_cache_ttl_s: int = 86_400

    # Session / transport
    max_concurrent_fetches: int = 8
    fetch_timeout_s: float = 30.0
    retry_attempts: int = 3
    retry_backoff_s: float = 1.0

    # Browser (nodriver)
    browser_mem_threshold_gib: float = 1.0

    @classmethod
    def from_env(cls) -> "FetchSettings":
        return cls(
            curl_cffi_pool_size=ENV.get_int("HLEDAC_CURL_CFFI_POOL_SIZE", 4),
            curl_cffi_prewarm=ENV.get_bool("HLEDAC_CURL_CFFI_PREWARM", True),
            curl_cffi_conditional_cache=ENV.get_bool("HLEDAC_CONDITIONAL_CACHE", True),
            http3_enabled=ENV.get_bool("HLEDAC_ENABLE_HTTPX_H3", True),
            http3_cache_max=ENV.get_int("HLEDAC_HTTP3_CACHE_MAX", 1024),
            http3_concurrency_max=ENV.get_int("HLEDAC_HTTP3_CONCURRENCY_MAX", 3),
            http3_timeout_s=ENV.get_float("HLEDAC_HTTP3_TIMEOUT_S", 8.0),
            http3_cache_ttl_s=ENV.get_int("HLEDAC_HTTP3_CACHE_TTL_S", 86_400),
            max_concurrent_fetches=ENV.get_int("HLEDAC_MAX_CONCURRENT_FETCHES", 8),
            fetch_timeout_s=ENV.get_float("HLEDAC_FETCH_TIMEOUT_S", 30.0),
            retry_attempts=ENV.get_int("HLEDAC_FETCH_RETRY_ATTEMPTS", 3),
            retry_backoff_s=ENV.get_float("HLEDAC_FETCH_RETRY_BACKOFF_S", 1.0),
            browser_mem_threshold_gib=ENV.get_float("HLEDAC_BROWSER_MEM_THRESHOLD_GIB", 1.0),
        )


class MLXSettings(msgspec.Struct, frozen=True, gc=False):
    """MLX / LLM inference configuration."""

    # kv_bits and max_kv_size go into mlx_lm.generate(), NOT load()
    kv_bits: int = 4
    max_kv_size: int = 8192

    # Hermes3 model
    hermes_model: str = "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"
    hermes_no_cache: bool = False
    half_precision: bool = True
    paged_kv_cache: bool = False
    kv_quantize: bool = False

    # Metal memory (M1 8GB ceiling: 1GiB)
    metal_cache_limit_gib: float = 1.0

    # Session / cache
    session_cache_memory_mb: int = 0  # 0 = auto
    session_cache_maxsize: int = 0    # 0 = auto
    idle_unload_timeout_s: float = 1800.0

    # DSPy / optimizer
    dspy_enabled: bool = False
    dspy_optimize: bool = False

    # Batch executor
    batch_max_concurrent: int = 4
    batch_timeout_s: float = 120.0

    # ANE embeddings
    ane_embed_batch_size: int = 32
    ane_dedup_threshold: float = 0.92

    @classmethod
    def from_env(cls) -> "MLXSettings":
        return cls(
            kv_bits=ENV.get_int("GHOST_KV_BITS", 4),
            max_kv_size=ENV.get_int("GHOST_KV_SIZE", 8192),
            hermes_model=os.environ.get(
                "HLEDAC_HERMES_MODEL",
                "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"
            ),
            hermes_no_cache=ENV.get_bool("HLEDAC_HERMES_NO_CACHE", False),
            half_precision=ENV.get_bool("HLEDAC_HALF_PRECISION", True),
            paged_kv_cache=ENV.get_bool("HLEDAC_PAGED_KV_CACHE", False),
            kv_quantize=ENV.get_bool("HLEDAC_KV_QUANTIZE", False),
            metal_cache_limit_gib=min(
                max(ENV.get_float("HLEDAC_METAL_CACHE_LIMIT_GIB", 1.0), 0.5),
                1.0,  # M1 8GB ceiling
            ),
            session_cache_memory_mb=ENV.get_int("HLEDAC_SESSION_CACHE_MEMORY_MB", 0),
            session_cache_maxsize=ENV.get_int("HLEDAC_SESSION_CACHE_MAXSIZE", 0),
            idle_unload_timeout_s=ENV.get_float("HLEDAC_IDLE_UNLOAD_TIMEOUT_S", 1800.0),
            dspy_enabled=ENV.get_bool("HLEDAC_ENABLE_DSPY", False),
            dspy_optimize=ENV.get_bool("HLEDAC_DSPY_OPTIMIZE", False),
            batch_max_concurrent=ENV.get_int("HLEDAC_BATCH_MAX_CONCURRENT", 4),
            batch_timeout_s=ENV.get_float("HLEDAC_BATCH_TIMEOUT_S", 120.0),
            ane_embed_batch_size=ENV.get_int("HLEDAC_ANE_EMBED_BATCH_SIZE", 32),
            ane_dedup_threshold=ENV.get_float("HLEDAC_ANE_DEDUP_THRESHOLD", 0.92),
        )


class DuckDBSettings(msgspec.Struct, frozen=True, gc=False):
    """DuckDB storage configuration."""

    in_process: bool = True   # HLEDAC_DUCKDB_INPROCESS (default ON, saves ~200MB RAM)
    threads: int = 2          # HLEDAC_DUCKDB_THREADS (2 optimal for M1 thread-local conn)
    checkpoint_policy: str = "auto"
    arrow_ingest: bool = True  # HLEDAC_ARROW_INGEST (default ON, zero-copy)

    # Memory (M1 8GB: 2GB default, 4GB ceiling)
    memory_limit_gib: float = 2.0
    memory_ceiling_gib: float = 4.0

    @classmethod
    def from_env(cls) -> "DuckDBSettings":
        return cls(
            in_process=ENV.get_bool("HLEDAC_DUCKDB_INPROCESS", True),
            threads=min(ENV.get_int("HLEDAC_DUCKDB_THREADS", 2), 4),  # M1 cap: 4
            checkpoint_policy=ENV.get_str("HLEDAC_DUCKDB_CHECKPOINT", "auto"),
            arrow_ingest=ENV.get_bool("HLEDAC_ARROW_INGEST", True),
            memory_limit_gib=min(ENV.get_float("HLEDAC_DUCKDB_MEMORY", 2.0), 4.0),
            memory_ceiling_gib=ENV.get_float("HLEDAC_DUCKDB_MEMORY_CEILING", 4.0),
        )


class DedupSettings(msgspec.Struct, frozen=True, gc=False):
    """Deduplication configuration."""

    lmdb_map_size: int = 256 * 1024 * 1024   # 256 MB
    hot_cache_max: int = 10_000
    max_ngrams: int = 5_000

    @classmethod
    def from_env(cls) -> "DedupSettings":
        return cls(
            lmdb_map_size=ENV.get_int("HLEDAC_DEDUP_LMDB_MAP_SIZE", 256 * 1024 * 1024),
            hot_cache_max=ENV.get_int("HLEDAC_DEDUP_HOT_CACHE_MAX", 10_000),
            max_ngrams=ENV.get_int("HLEDAC_DEDUP_MAX_NGRAMS", 5_000),
        )


class TransportSettings(msgspec.Struct, frozen=True, gc=False):
    """Tor / I2P / Nym transport configuration."""

    tor_enabled: bool = False
    i2p_enabled: bool = False
    nym_enabled: bool = False
    # OPSEC-001: socks5h:// forces remote DNS resolution by proxy.
    tor_proxy: str = "socks5h://127.0.0.1:9050"
    i2p_proxy: str = "socks5h://127.0.0.1:4444"
    # OPSEC-001: socks5h:// forces remote DNS resolution by Nym mixnet proxy.
    nym_proxy: str = "socks5h://127.0.0.1:1080"

    # DHT
    dht_enabled: bool = False
    dht_max_peers: int = 100
    dht_rpc_timeout_s: float = 10.0

    @classmethod
    def from_env(cls) -> "TransportSettings":
        return cls(
            tor_enabled=ENV.get_bool("HLEDAC_ENABLE_TOR", False),
            i2p_enabled=ENV.get_bool("HLEDAC_ENABLE_I2P", False),
            nym_enabled=ENV.get_bool("HLEDAC_ENABLE_NYM", False),
            # OPSEC-001: socks5h:// forces remote DNS resolution by Tor proxy. Port 9050 is standard Tor SOCKS.
            tor_proxy=ENV.get_str("HLEDAC_TOR_PROXY", "socks5h://127.0.0.1:9050"),
            # OPSEC-001: socks5h:// forces remote DNS resolution by I2P proxy. Port 4444 is standard I2P SOCKS, NOT 9050.
            i2p_proxy=ENV.get_str("HLEDAC_I2P_PROXY", "socks5h://127.0.0.1:4444"),
            # OPSEC-001: socks5h:// forces remote DNS resolution by Nym mixnet proxy.
            nym_proxy=ENV.get_str("HLEDAC_NYM_PROXY", "socks5h://127.0.0.1:1080"),
            dht_enabled=ENV.get_bool("HLEDAC_ENABLE_DHT", False),
            dht_max_peers=ENV.get_int("HLEDAC_DHT_MAX_PEERS", 100),
            dht_rpc_timeout_s=ENV.get_float("HLEDAC_DHT_RPC_TIMEOUT_S", 10.0),
        )


class MemorySettings(msgspec.Struct, frozen=True, gc=False):
    """Memory management / UMA pressure relief configuration."""

    # M1 8GB UMA limits
    memory_limit_mb: float = 5500.0
    thermal_threshold_c: float = 85.0

    # Pressure relief
    gc_cycle_interval_s: float = 30.0
    mlx_cache_clear_interval: int = 10

    # Swap guards (M1: no silent swap — relaxed=False)
    allow_swap: bool = False
    soft_ceiling_gib: float = 5.5  # fetch concurrency hard-caps here

    # Resource governor thresholds
    threshold_soft_warn_gib: float = 6.8
    threshold_warn_gib: float = 7.0
    threshold_critical_gib: float = 7.5
    threshold_emergency_gib: float = 7.8
    hysteresis_exit_gib: float = 6.8

    @classmethod
    def from_env(cls) -> "MemorySettings":
        return cls(
            memory_limit_mb=ENV.get_float("HLEDAC_MEMORY_LIMIT_MB", 5500.0),
            thermal_threshold_c=ENV.get_float("HLEDAC_THERMAL_THRESHOLD_C", 85.0),
            gc_cycle_interval_s=ENV.get_float("HLEDAC_GC_CYCLE_INTERVAL_S", 30.0),
            mlx_cache_clear_interval=ENV.get_int("HLEDAC_MLX_CACHE_CLEAR_INTERVAL", 10),
            allow_swap=ENV.get_bool("HLEDAC_ALLOW_SWAP", False),
            soft_ceiling_gib=ENV.get_float("HLEDAC_SOFT_CEILING_GIB", 5.5),
            threshold_soft_warn_gib=ENV.get_float("HLEDAC_RG_THRESHOLD_SOFT_WARN_GIB", 6.8),
            threshold_warn_gib=ENV.get_float("HLEDAC_RG_THRESHOLD_WARN_GIB", 7.0),
            threshold_critical_gib=ENV.get_float("HLEDAC_RG_THRESHOLD_CRITICAL_GIB", 7.5),
            threshold_emergency_gib=ENV.get_float("HLEDAC_RG_THRESHOLD_EMERGENCY_GIB", 7.8),
            hysteresis_exit_gib=ENV.get_float("HLEDAC_RG_HYSTERESIS_EXIT_GIB", 6.8),
        )


class SprintSettings(msgspec.Struct, frozen=True, gc=False):
    """Sprint lifecycle / timing configuration."""

    default_duration_s: float = 1800.0   # 30 min
    default_windup_lead_s: float = 180.0  # 3 min before end
    default_cycle_sleep_s: float = 5.0

    # Active window guards (F221)
    min_active_window_s: float = 30.0
    min_sprint_duration_s: float = 60.0

    # Adaptive ratios (F250)
    windup_ratio_aggressive: float = 0.15
    windup_ratio_quick: float = 0.20   # ≤120s
    windup_ratio_short: float = 0.25   # ≤300s
    windup_ratio_default: float = 0.30

    @classmethod
    def from_env(cls) -> "SprintSettings":
        return cls(
            default_duration_s=ENV.get_float("HLEDAC_SPRINT_DURATION_S", 1800.0),
            default_windup_lead_s=ENV.get_float("HLEDAC_WINDUP_LEAD_S", 180.0),
            default_cycle_sleep_s=ENV.get_float("HLEDAC_CYCLE_SLEEP_S", 5.0),
            min_active_window_s=ENV.get_float("HLEDAC_MIN_ACTIVE_WINDOW_S", 30.0),
            min_sprint_duration_s=ENV.get_float("HLEDAC_MIN_SPRINT_DURATION_S", 60.0),
            windup_ratio_aggressive=ENV.get_float("HLEDAC_WINDUP_RATIO_AGGRESSIVE", 0.15),
            windup_ratio_quick=ENV.get_float("HLEDAC_WINDUP_RATIO_QUICK", 0.20),
            windup_ratio_short=ENV.get_float("HLEDAC_WINDUP_RATIO_SHORT", 0.25),
            windup_ratio_default=ENV.get_float("HLEDAC_WINDUP_RATIO_DEFAULT", 0.30),
        )


class GraphSettings(msgspec.Struct, frozen=True, gc=False):
    """DuckPGQ / entity graph configuration."""

    graph_enabled: bool = False
    max_hops: int = 3
    max_candidates: int = 1000
    hot_cache_max: int = 512

    @classmethod
    def from_env(cls) -> "GraphSettings":
        return cls(
            graph_enabled=ENV.get_bool("HLEDAC_ENABLE_GRAPH_ANALYSIS", False),
            max_hops=ENV.get_int("HLEDAC_GRAPH_MAX_HOPS", 3),
            max_candidates=ENV.get_int("HLEDAC_GRAPH_MAX_CANDIDATES", 1000),
            hot_cache_max=ENV.get_int("HLEDAC_GRAPH_HOT_CACHE_MAX", 512),
        )


class SynthesisSettings(msgspec.Struct, frozen=True, gc=False):
    """Hermes3 / synthesis lane configuration."""

    hermes_enabled: bool = False
    hermes_synthesis_enabled: bool = False
    hermes_budget_ratio: float = 0.35  # 35% of active window

    deep_hermes_enabled: bool = False
    pydantic_validation: bool = False

    @classmethod
    def from_env(cls) -> "SynthesisSettings":
        return cls(
            hermes_enabled=ENV.get_bool("HLEDAC_ENABLE_LLM", False),
            hermes_synthesis_enabled=ENV.get_bool("HLEDAC_ENABLE_HERMES_SYNTHESIS", False),
            hermes_budget_ratio=ENV.get_float("HLEDAC_HERMES_BUDGET_RATIO", 0.35),
            deep_hermes_enabled=ENV.get_bool("HLEDAC_ENABLE_DEEPHERMES", False),
            pydantic_validation=ENV.get_bool("HLEDAC_DEEPHERMES_PYDANTIC_VALIDATION", False),
        )


class CooldownSettings(msgspec.Struct, frozen=True, gc=False):
    """Source cooldown / circuit breaker settings."""

    # Circuit breaker (from CB_CONFIG_DEFAULTS)
    cb_max_tracked_domains: int = 500
    cb_max_recovery_timeout_s: float = 120.0
    cb_boot_recovery_timeout_s: float = 5.0
    cb_base_recovery_timeout_s: float = 15.0
    cb_boot_phase_duration_s: float = 60.0
    cb_failure_threshold: int = 5  # M1AirConfig.circuit_breaker_threshold=5 (tightened for M1 thermal envelope)
    cb_half_open_probes: int = 3
    cb_timeout_accumulator_weight: float = 0.5
    cb_consecutive_timeout_threshold: int = 4
    cb_jitter_min_mult: float = 0.5
    cb_jitter_max_mult: float = 1.5
    cb_jitter_min_fraction: float = 0.1

    # Source family cooldown
    cooldown_base_s: float = 60.0
    cooldown_max_s: float = 600.0
    cooldown_backoff_factor: float = 1.5

    @classmethod
    def from_env(cls) -> "CooldownSettings":
        return cls(
            cb_max_tracked_domains=ENV.get_int("HLEDAC_CB_MAX_TRACKED_DOMAINS", 500),
            cb_max_recovery_timeout_s=ENV.get_float("HLEDAC_CB_MAX_RECOVERY_TIMEOUT_S", 120.0),
            cb_boot_recovery_timeout_s=ENV.get_float("HLEDAC_CB_BOOT_RECOVERY_TIMEOUT_S", 5.0),
            cb_base_recovery_timeout_s=ENV.get_float("HLEDAC_CB_BASE_RECOVERY_TIMEOUT_S", 15.0),
            cb_boot_phase_duration_s=ENV.get_float("HLEDAC_CB_BOOT_PHASE_DURATION_S", 60.0),
            cb_failure_threshold=ENV.get_int("HLEDAC_CB_CIRCUIT_FAILURE_THRESHOLD", 5),  # M1AirConfig=5
            cb_half_open_probes=ENV.get_int("HLEDAC_CB_CIRCUIT_HALF_OPEN_PROBES", 3),
            cb_timeout_accumulator_weight=ENV.get_float("HLEDAC_CB_TIMEOUT_ACCUMULATOR_WEIGHT", 0.5),
            cb_consecutive_timeout_threshold=ENV.get_int("HLEDAC_CB_CONSECUTIVE_TIMEOUT_THRESHOLD", 4),
            cb_jitter_min_mult=ENV.get_float("HLEDAC_CB_JITTER_MIN_MULT", 0.5),
            cb_jitter_max_mult=ENV.get_float("HLEDAC_CB_JITTER_MAX_MULT", 1.5),
            cb_jitter_min_fraction=ENV.get_float("HLEDAC_CB_JITTER_MIN_FRACTION", 0.1),
            cooldown_base_s=ENV.get_float("HLEDAC_COOLDOWN_BASE_S", 60.0),
            cooldown_max_s=ENV.get_float("HLEDAC_COOLDOWN_MAX_S", 600.0),
            cooldown_backoff_factor=ENV.get_float("HLEDAC_COOLDOWN_BACKOFF_FACTOR", 1.5),
        )


# ---------------------------------------------------------------------------
# Feature Gates (msgspec.Struct — computed once, cached)
# ---------------------------------------------------------------------------


class FeatureGates(msgspec.Struct, frozen=True, gc=False):
    """Feature flag gates — computed lazily once at first access."""

    # CT / threat intelligence
    academic: bool = False
    alt_protocols: bool = False
    banner_grab: bool = False
    bgp: bool = False
    bgp_pdns: bool = False
    captcha_detection: bool = False
    censys: bool = False
    commoncrawl: bool = False
    dark_pivots: bool = False
    dht: bool = False
    fediverse: bool = False
    gopher: bool = False
    graph_analysis: bool = False
    graph_rag: bool = False
    greynoise: bool = False
    heavy_browser: bool = False
    hypothesis: bool = False
    image_osint: bool = False
    ipfs: bool = False
    leaksentinel: bool = False
    layers: bool = False
    nym: bool = False
    privacy_layer: bool = False
    providerless_discovery: bool = False
    research_layer: bool = False
    shodan: bool = False
    social: bool = False
    stealth_layer: bool = False
    steganography: bool = False
    synthesis: bool = False
    ti_feeds: bool = False
    tor: bool = False
    zero_attribution: bool = False
    i2p: bool = False
    curl_cffi: bool = False
    httpx_h2: bool = False
    httpx_h3: bool = False

    @classmethod
    def from_env(cls) -> "FeatureGates":
        return cls(
            academic=ENV.get_bool("HLEDAC_ENABLE_ACADEMIC"),
            alt_protocols=ENV.get_bool("HLEDAC_ENABLE_ALT_PROTOCOLS"),
            banner_grab=ENV.get_bool("HLEDAC_ENABLE_BANNER_GRAB"),
            bgp=ENV.get_bool("HLEDAC_ENABLE_BGP"),
            bgp_pdns=ENV.get_bool("HLEDAC_ENABLE_BGP_PDNS"),
            captcha_detection=ENV.get_bool("HLEDAC_ENABLE_CAPTCHA_DETECTION"),
            censys=ENV.get_bool("HLEDAC_ENABLE_CENSYS"),
            commoncrawl=ENV.get_bool("HLEDAC_ENABLE_COMMONCRAWL"),
            dark_pivots=ENV.get_bool("HLEDAC_ENABLE_DARK_PIVOTS"),
            dht=ENV.get_bool("HLEDAC_ENABLE_DHT"),
            fediverse=ENV.get_bool("HLEDAC_ENABLE_FEDIVERSE"),
            gopher=ENV.get_bool("HLEDAC_ENABLE_GOPHER"),
            graph_analysis=ENV.get_bool("HLEDAC_ENABLE_GRAPH_ANALYSIS"),
            graph_rag=ENV.get_bool("HLEDAC_ENABLE_GRAPH_RAG"),
            greynoise=ENV.get_bool("HLEDAC_ENABLE_GREYNOISE"),
            heavy_browser=ENV.get_bool("HLEDAC_ENABLE_HEAVY_BROWSER"),
            hypothesis=ENV.get_bool("HLEDAC_ENABLE_HYPOTHESIS"),
            image_osint=ENV.get_bool("HLEDAC_ENABLE_IMAGE_OSINT"),
            ipfs=ENV.get_bool("HLEDAC_ENABLE_IPFS"),
            leaksentinel=ENV.get_bool("HLEDAC_ENABLE_LEAKSENTINEL"),
            layers=ENV.get_bool("HLEDAC_ENABLE_LAYERS"),
            nym=ENV.get_bool("HLEDAC_ENABLE_NYM"),
            privacy_layer=ENV.get_bool("HLEDAC_ENABLE_PRIVACY_LAYER"),
            providerless_discovery=ENV.get_bool("HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY"),
            research_layer=ENV.get_bool("HLEDAC_ENABLE_RESEARCH_LAYER"),
            shodan=ENV.get_bool("HLEDAC_ENABLE_SHODAN"),
            social=ENV.get_bool("HLEDAC_ENABLE_SOCIAL"),
            stealth_layer=ENV.get_bool("HLEDAC_ENABLE_STEALTH_LAYER"),
            steganography=ENV.get_bool("HLEDAC_ENABLE_STEGANOGRAPHY"),
            synthesis=ENV.get_bool("HLEDAC_ENABLE_SYNTHESIS"),
            ti_feeds=ENV.get_bool("HLEDAC_ENABLE_TI_FEEDS"),
            tor=ENV.get_bool("HLEDAC_ENABLE_TOR"),
            zero_attribution=ENV.get_bool("HLEDAC_ENABLE_ZERO_ATTRIBUTION"),
            i2p=ENV.get_bool("HLEDAC_ENABLE_I2P"),
            curl_cffi=ENV.get_bool("HLEDAC_ENABLE_CURL_CFFI"),
            httpx_h2=ENV.get_bool("HLEDAC_ENABLE_HTTPX_H2"),
            httpx_h3=ENV.get_bool("HLEDAC_ENABLE_HTTPX_H3"),
        )


# ---------------------------------------------------------------------------
# Canonical Settings singleton
# ---------------------------------------------------------------------------

class Settings(msgspec.Struct, frozen=True, gc=False):
    """
    Canonical Settings for Hledac Universal OSINT Orchestrator.

    Single source of truth for all HLEDAC_* configuration.
    Lazily resolved from ENV on first instantiation.

    Usage:
        from config.settings import settings

        if settings.fetch.http3_enabled:
            ...

        pool_size = settings.fetch.curl_cffi_pool_size

        if settings.features.dark_pivots:
            ...

    M1 8GB invariants:
        - DuckDB threads capped at 4
        - Metal cache ceiling: 1 GiB
        - Memory soft ceiling: 5.5 GiB
        - kv_bits=4, max_kv_size=8192 in generate() not load()
    """

    fetch: FetchSettings = FetchSettings()
    mlx: MLXSettings = MLXSettings()
    duckdb: DuckDBSettings = DuckDBSettings()
    dedup: DedupSettings = DedupSettings()
    transport: TransportSettings = TransportSettings()
    memory: MemorySettings = MemorySettings()
    sprint: SprintSettings = SprintSettings()
    graph: GraphSettings = GraphSettings()
    synthesis: SynthesisSettings = SynthesisSettings()
    cooldown: CooldownSettings = CooldownSettings()
    features: FeatureGates = FeatureGates()

    @classmethod
    def from_env(cls) -> "Settings":
        """Build Settings from ENV — called once at startup."""
        return cls(
            fetch=FetchSettings.from_env(),
            mlx=MLXSettings.from_env(),
            duckdb=DuckDBSettings.from_env(),
            dedup=DedupSettings.from_env(),
            transport=TransportSettings.from_env(),
            memory=MemorySettings.from_env(),
            sprint=SprintSettings.from_env(),
            graph=GraphSettings.from_env(),
            synthesis=SynthesisSettings.from_env(),
            cooldown=CooldownSettings.from_env(),
            features=FeatureGates.from_env(),
        )


# ---------------------------------------------------------------------------
# Process-wide singleton with lazy initialization
# ---------------------------------------------------------------------------

_settings: Settings | None = None
_settings_lock = threading.Lock()


def settings() -> Settings:
    """
    Return the process-wide Settings singleton.

    Initialized once on first call (thread-safe, lazy).
    All domain structs are frozen (immutable) — safe to share across threads.
    """
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = Settings.from_env()
    return _settings


# ---------------------------------------------------------------------------
# Backward compatibility — re-export ENV for raw / dynamic lookups
# ---------------------------------------------------------------------------

def __getattr__(name: str) -> Any:
    """Route missing attrs to ENV for backward compat."""
    if name == "ENV":
        from hledac.universal.core.env_config import ENV as _ENV
        return _ENV
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
