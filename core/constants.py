"""
core/constants.py — Canonical Source of Truth for Magic Numbers

Sprint F270: Centralized timeout and bound constants.

Tento modul je JEDINÝ zdroj pravdy pro všechny hardcoded integer/float
literals s sémantickým významem (časové limity, memory bounds, rate limity,
cache velikosti, protocol constants). Cokoliv jinde v kódu používá
přímý literal, je tech debt — oprav směřuj sem.

Organizace podle domény:
    NetworkTimeouts    — HTTP/HTTPS fetch timeouty a retry policy
    M1MemoryBounds    — M1 8GB UMA specifické memory/cache limity
    MLXInference      — KV cache, batch, token limity pro MLX inference
    ProtocolPorts     — Tor, I2P, a další protokolové porty
    HTTPCodes         — HTTP status kódy pro retry/error policy
    SemanticRatios    — Plovoucí konstanty s empiricky odvozenou sémantikou

Pravidla:
    1. Žádné importy z jiných hledac modulů na úrovni modulu
       (pouze stdlib + typing pro TYPE_CHECKING)
    2. Všechny hodnoty musí mít typovou annotaci
    3. Docstringy musí vysvětlit PROČ je hodnota taková jaká je
    4. Hodnoty odvozené od M1 8GB UMA se počítají z _detect_uma()
    5. Mezimodulové závislosti řeš přes TYPE_CHECKING + lazy import

Přístup pro nové konstanty:
    1. Přidej do příslušné dataclassy
    2. Exportuj v __all__
    3. Pokud je konstanta použita v >1 modulu, musí být v TOMTO souboru
"""


import os
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

__all__ = [
    # Dataclasses
    "NetworkTimeouts",
    "M1MemoryBounds",
    "MLXInference",
    "ProtocolPorts",
    "HTTPCodes",
    "SemanticRatios",
    "DuckDBStorage",
    # Singletony (lazy initialized)
    "NETWORK",
    "M1_BOUNDS",
    "MLX",
    "PORTS",
    "HTTP",
    "RATIOS",
    "DUCKDB",
    # Helper
    "get_m1_uma_mb",
]


# ---------------------------------------------------------------------------
# Hardware Detection (M1 8GB UMA)
# ---------------------------------------------------------------------------

def _detect_uma_mb() -> int:
    """
    Detect unified memory size in MB.

    Falls back to 8192 MB (M1 8GB) if detection fails.
    This is the canonical value for all M1_BOUNDS derivations.
    """
    try:
        import psutil

        return int(psutil.virtual_memory().total // (1024 * 1024))
    except Exception:
        return 8_192  # M1 8GB fallback


_UMA_TOTAL_MB: int = _detect_uma_mb()


def get_m1_uma_mb() -> int:
    """Public accessor for detected UMA size."""
    return _UMA_TOTAL_MB


# ---------------------------------------------------------------------------
# Network Timeouts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NetworkTimeouts:
    """
    HTTP/HTTPS network timeouty — všechny sítové operace.

    Zásady:
    - Timeouty jsou ceiling — operace musí skončit DŘÍV než vyprší časovač
    - Pro per-request timeouty platí: timeout <= ceil(zbytek_sprintu / 2)
    - Fail-soft: timeout na úrovni requestu neznamená crash — vrací None/[]
    """

    # curl_cffi session timeout (TLS handshake + response)
    # Historické: 10s pokrývá Chrome→server RTT + TLS 1.3 handshake
    curl_cffi_session: float = 10.0

    # HTTP/3 per-request hard cap
    # 8s pokrývá QUIC handshake (~2ms na LAN) + server processing
    http3_request: float = 8.0

    # HTTP/3 semaphore wait (non-blocking acquire)
    # 2s — dost naquota pro 5 souběžných handshakes
    http3_semaphore_wait: float = 2.0

    # Speculative Alt-Svc HEAD probe timeout
    # 4s — bounded, M1 8GB friendly
    http3_probe: float = 4.0

    # DuckDBShadowStore vacuum threshold check (async, low priority)
    duckdb_vacuum: float = 30.0

    # Branch timeout pro sprint scheduler — derivované
    branch_base: float = 45.0  # aggressive mode max
    branch_min_remaining: float = 2.0  # floor pro krátké sprinty

    # DuckDB async coalescer stop
    coalescer_stop: float = 10.0

    # Pressure relief task
    pressure_relief: float = 5.0

    # Lock acquire (DuckDB write lock)
    lock_acquire: float = 5.0

    # MLX worker thread bridge
    mlx_bridge: float = 30.0  # max inference time

    # Batch scheduler shutdown
    batch_shutdown: float = 3.0

    # Pre-flight guard (F221-ABORT)
    min_active_window: float = 30.0  # seconds

    # Fetch coordinator idle between batches
    fetch_idle: float = 0.25  # asyncio.sleep(0.25) v shutdown path

    # Sprint 4B: Canonical fetch timeouts (replicated in coordinators/fetch_coordinator.py)
    # These are the SSOT — fetch_coordinator.py imports from here, not the other way around
    clearnet_api: float = 20.0   # seconds - API JSON endpoints
    clearnet_html: float = 35.0  # seconds - HTML page fetch
    tor: float = 75.0            # seconds - .onion over Tor
    i2p: float = 150.0           # seconds - .i2p over I2P
    gopher: float = 30.0         # seconds - gopher protocol fetch


# ---------------------------------------------------------------------------
# M1 8GB Memory Bounds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class M1MemoryBounds:
    """
    M1 8GB UMA specifické memory a cache limity.

    Odvozeno z:
    - _UMA_TOTAL_MB (detekovaná RAM, typicky 8192 MB)
    - 88% soft ceiling pro fetch operace
    - Metal cache ceiling 1 GiB (hardcoded v MLX)
    - KV cache budget ~750 MB při plném vytížení
    """

    # Soft ceiling pro fetch — 88% UMA = bezpečná hranice pro network operace
    # Na 8GB: 7.04 GB | Na 16GB: 14.08 GB
    fetch_soft_ceiling_gb: float = field(
        default_factory=lambda: round(_UMA_TOTAL_MB / 1024 * 0.88, 2)
    )

    # Metal cache ceiling (MLX hard limit, nelze překročit)
    # 1 GiB = MLX maximum pro 4bit modely na M1
    metal_cache_ceiling_mb: int = 1_024

    # KV cache headroom pro burst — 512 MB
    kv_cache_headroom_mb: int = 512

    # RSS probe timeout (psutil, non-blocking)
    rss_probe_timeout_s: float = 0.05

    # LMDB dedup map size (64 MB — dedup metadata jsou malá)
    dedup_lmdb_map_mb: int = 64

    # LMDB conditional cache map size (16 MB)
    conditional_cache_lmdb_mb: int = 16

    # DuckDB WAL autocheckpoint (256 MB — O3 optimalizace)
    duckdb_wal_checkpoint_mb: int = 256

    # HTTP cache (SQLite) max size (256 MB)
    http_cache_mb: int = 256

    # HTTP cache TTL (Bing/DDG SERP freshness window)
    http_cache_ttl_s: int = 86_400  # 24h

    # H3 LRU cache max entries (2k hosts × ~2KB/entry ≈ 4MB RAM)
    http3_lru_max: int = 2048

    # H3 concurrency max (QUIC handshakes souběžně)
    http3_concurrency_max: int = 5

    # curl_cffi host session cache max hosts
    curl_host_session_max: int = 20

    # curl_cffi host session TTL (5 min idle)
    curl_host_session_ttl_s: float = 300.0

    # curl_cffi prewarm pool size (4 sessions round-robin)
    curl_prewarm_pool_size: int = 4

    # Circuit breaker max tracked domains (LRU eviction)
    circuit_breaker_max_domains: int = 512

    # Fetch coordinator frontier max size
    frontier_max_size: int = 1_000

    # Fetch coordinator evidence IDs max
    evidence_ids_max: int = 500

    # Fetch coordinator domain failures max before circuit open
    domain_failures_max: int = 1_000

    # Fetch coordinator domain failure cutoff (24h)
    domain_failure_cutoff_s: int = 86_400  # 24 * 3600

    # DuckDB batch ingest buffer (2 GB vacuum threshold)
    duckdb_vacuum_threshold_mb: int = 2_048


# ---------------------------------------------------------------------------
# MLX Inference
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MLXInference:
    """
    MLX inference limity — KV cache, batch, token boundy.

    Odvozeno z:
    - Model: Hermes-3-Llama-3.2-3B-4bit (~2GB RAM)
    - Context window: 8192 tokens
    - M1 Metal cache ceiling: 1 GiB
    - KV cache při 4bit quantization: ~0.75 GB
    """

    # Context window (max tokens pro model)
    context_window: int = 8192

    # Default max tokens pro generation
    default_max_tokens: int = 1024

    # System prompt cache KV size
    system_prompt_cache_kv: int = 512

    # Warmup cache tokens
    warmup_cache_tokens: int = 1000

    # Max KV cache size — adaptivní, ceiling = 8192
    kv_cache_max: int = 8192

    # Min KV cache size (floor pro malé prompty)
    kv_cache_min: int = 512

    # Batch queue max depth (PriorityQueue maxsize)
    batch_queue_max: int = 256

    # Batch high pressure depth (triggers 0.3s flush)
    batch_high_pressure_depth: int = 192

    # Batch medium pressure depth (triggers 0.7s flush)
    batch_medium_pressure_depth: int = 64

    # Max items per batch
    batch_max_size: int = 8

    # Batch EMA smoothing alpha
    batch_ema_alpha: float = 0.3

    # Throughput high threshold (items/s) → fast flush
    batch_throughput_high: float = 10.0

    # Throughput low threshold (items/s) → slow flush
    batch_throughput_low: float = 1.0

    # Flush interval boundaries (seconds)
    flush_fast: float = 0.3
    flush_medium: float = 0.7
    flush_default: float = 2.0

    # Age bump interval (flush cycles mezi priority bump)
    age_bump_interval: int = 3

    # Token length bins pro batch segregation
    length_bin_short: int = 256
    length_bin_medium: int = 1024

    # Prompt char limits
    max_prompt_chars: int = 8192
    max_system_msg_chars: int = 8192

    # Long prompt threshold (>12k chars = skip batch)
    prompt_long_threshold: int = 12000

    # Long max_tokens threshold (>2048 = skip batch)
    tokens_long_threshold: int = 2048

    # Memory pressure thresholds (derived from uma_budget.py SSOT)
    memory_warn_threshold: float = 6.0  # GB
    memory_critical_threshold: float = 6.5  # GB
    memory_emergency_threshold: float = 7.0  # GB

    # M3 Metal pressure trigger (2 GiB)
    metal_pressure_bytes: int = 2 * 1024 * 1024 * 1024

    # MLX worker thread memory EMA alpha
    memory_ema_alpha: float = 0.15

    # PID controller gains (memory pressure response)
    pid_kp: float = 0.5
    pid_ki: float = 0.05
    pid_kd: float = 0.1

    # LoRA max KV reduction
    lora_kv_reduction_factor: int = 2  # 8192→4096 when LoRA active


# ---------------------------------------------------------------------------
# Protocol Ports
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtocolPorts:
    """
    Protocol-specific port numbers.

    Všechny porty jsou konfigurovatelné přes ENV vars.
    """

    # Tor SOCKS proxy
    tor_socks: int = 9050
    tor_control: int = 9051
    tor_max_circuit_dirtiness: int = 600  # seconds

    # I2P SOCKS and SAM
    i2p_socks: int = 7654
    i2p_sam: int = 7656

    # I2P HTTP proxy (Freenet FProxy)
    i2p_http: int = 8888

    # Default HTTPS
    https: int = 443


# ---------------------------------------------------------------------------
# HTTP Status Codes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HTTPCodes:
    """
    HTTP status kódy pro retry/error policy.

    Duplikace PŘÍSĚ zakázána — všechny moduly používají HTTP.retryable
    """

    # Retryable status codes (server-side failures, retry helps)
    retryable: frozenset[int] = field(default_factory=lambda: frozenset({429, 502, 503, 504, 520}))

    # Status codes that trigger escalation to curl_cffi
    escalation_trigger: frozenset[int] = field(default_factory=lambda: frozenset({401, 403}))

    # Success range start
    success_min: int = 200
    success_max: int = 299

    # Redirect range
    redirect_min: int = 300
    redirect_max: int = 399


# ---------------------------------------------------------------------------
# Semantic Ratios
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticRatios:
    """
    Empiricky odvozené plovoucí konstanty.

    Každá hodnota má poznámku ODKUD pochází (benchmark, historical fix, etc.)
    """

    # Windup ratios (sprint duration fractions for graceful shutdown)
    # F250: 30% normal, 15% aggressive, clamped [30, 180]s
    windup_normal: float = 0.30
    windup_aggressive: float = 0.15
    windup_clamp_min: float = 30.0
    windup_clamp_max: float = 180.0

    # Active window efficiency threshold for F289-WINDUP
    windup_efficiency_critical: float = 0.40

    # Branch remaining time floor (5% of remaining)
    # Formula: max(2.0, 0.15 * remaining_s)
    branch_remaining_ratio: float = 0.15
    branch_min_remaining: float = 2.0
    branch_max_remaining: float = 5.0

    # Dominance threshold (feed over non-feed)
    dominance_ratio: float = 0.95

    # Memory pressure thresholds
    memory_pressure_safe: float = 0.70  # below this = MLX safe
    memory_pressure_warn: float = 0.75  # AIMD decrease factor

    # AIMD
    aimd_decrease_factor: float = 0.75  # multiply by this on failure (25% reduction)
    aimd_increase_factor: float = 0.25  # additive increase on success

    # Confidence thresholds for DuckDB semantic dedup
    # Feed sources have lower bar (more noise)
    dedup_semantic_feed: float = 0.75
    dedup_semantic_default: float = 0.80
    dedup_semantic_strict: float = 0.85

    # Cover traffic rate (percentage of successful fetches to add decoy)
    cover_traffic_rate: float = 0.05  # 5% — low to avoid noise

    # DuckDB free RAM threshold for operations
    free_ram_min_mb: float = 256.0

    # Finding funnel thresholds
    funnel_text_rate: float = 100.0  # percentage

    # Sprint windup efficiency thresholds
    windup_efficiency_acceptable: float = 0.70


# ---------------------------------------------------------------------------
# DuckDB Storage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DuckDBStorage:
    """
    DuckDB-specific storage bounds.
    """

    # Batch size for arrow ingest (tuned for M1 8GB)
    batch_ingest_size: int = 2048

    # Max pending sync markers before oldest eviction
    pending_sync_markers_max: int = 10_000

    # Replay chunk size (markers per chunk)
    replay_chunk_size: int = 100

    # Query result limits
    query_result_limit: int = 10_000
    query_default_limit: int = 1000

    # Historical cutoff for temporal queries
    temporal_cutoff_days: int = 5
    temporal_cutoff_s: int = 5 * 86_400  # 5 * 24 * 3600

    # Semantic dedup thresholds
    dedup_semantic_feed: float = 0.75
    dedup_semantic_default: float = 0.80
    dedup_semantic_strict: float = 0.85

    # Embedding dimension (MLX)
    embedding_dim: int = 256

    # Max pending futures in inference engine
    pending_futures_max: int = 256

    # Eval granularity tokens
    eval_granularity_tokens: int = 256
    clear_granularity_tokens: int = 256

    # Fallback cache bytes (32 MB)
    fallback_cache_bytes: int = 32 * 1024 * 1024

    # Min body cache bytes (skip <256 byte responses)
    min_body_cache_bytes: int = 256

    # Max body cache bytes (2 MB hard cap)
    max_body_cache_bytes: int = 2 * 1024 * 1024

    # LMDB dedup map
    dedup_lmdb_map_size: int = 64 * 1024 * 1024

    # Yield per minute floor (prevent div by zero)
    yield_per_min_floor: float = 0.001


# ---------------------------------------------------------------------------
# Lazy Singleton Accessors
# ---------------------------------------------------------------------------


def _make_lazy(cls):
    """Create a lazily-initialized singleton for the given dataclass."""
    instance = None

    def get():
        nonlocal instance
        if instance is None:
            instance = cls()
        return instance

    return get


# Module-level lazy singletons.
# Usage: from core.constants import NETWORK; NETWORK.curl_cffi_session
# These are initialized on first access to avoid side-effects at import time.
NETWORK = _make_lazy(NetworkTimeouts)
M1_BOUNDS = _make_lazy(M1MemoryBounds)
MLX = _make_lazy(MLXInference)
PORTS = _make_lazy(ProtocolPorts)
HTTP = _make_lazy(HTTPCodes)
RATIOS = _make_lazy(SemanticRatios)
DUCKDB = _make_lazy(DuckDBStorage)
