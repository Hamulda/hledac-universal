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

from dataclasses import field

from compat.msgspec_gc_compat import Struct

# MODERN-36/37/38 Fix: SSOT imports for UmaBudget constants
# Import at module level to enable class attribute derivation
from hledac.universal.utils.uma_budget import UmaBudget

__all__ = [
    "NetworkTimeouts",
    "M1MemoryBounds",
    "MLXInference",
    "ProtocolPorts",
    "HTTPCodes",
    "SemanticRatios",
    "DuckDBStorage",
    "NETWORK",
    "M1_BOUNDS",
    "MLX",
    "PORTS",
    "HTTP",
    "RATIOS",
    "DUCKDB",
    "get_m1_uma_mb",
]


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
        return 8192


_UMA_TOTAL_MB: int = _detect_uma_mb()


def get_m1_uma_mb() -> int:
    """Public accessor for detected UMA size."""
    return _UMA_TOTAL_MB


class NetworkTimeouts(Struct, frozen=True):
    """
    HTTP/HTTPS network timeouty — všechny sítové operace.

    Zásady:
    - Timeouty jsou ceiling — operace musí skončit DŘÍV než vyprší časovač
    - Pro per-request timeouty platí: timeout <= ceil(zbytek_sprintu / 2)
    - Fail-soft: timeout na úrovni requestu neznamená crash — vrací None/[]
    """

    curl_cffi_session: float = 10.0
    http3_request: float = 8.0
    http3_semaphore_wait: float = 2.0
    http3_probe: float = 4.0
    duckdb_vacuum: float = 30.0
    branch_base: float = 45.0
    branch_min_remaining: float = 2.0
    coalescer_stop: float = 10.0
    pressure_relief: float = 5.0
    lock_acquire: float = 5.0
    mlx_bridge: float = 30.0
    batch_shutdown: float = 3.0
    min_active_window: float = 30.0
    fetch_idle: float = 0.25
    clearnet_api: float = 20.0
    clearnet_html: float = 35.0
    tor: float = 75.0
    i2p: float = 150.0
    gopher: float = 30.0


class M1MemoryBounds(Struct, frozen=True):
    """
    M1 8GB UMA specifické memory a cache limity.

    Odvozeno z:
    - _UMA_TOTAL_MB (detekovaná RAM, typicky 8192 MB)
    - 88% soft ceiling pro fetch operace
    - Metal cache ceiling 1 GiB (hardcoded v MLX)
    - KV cache budget ~750 MB při plném vytížení
    """

    fetch_soft_ceiling_gb: float = field(default_factory=lambda: round(_UMA_TOTAL_MB / 1024 * 0.88, 2))
    metal_cache_ceiling_mb: int = 1024
    kv_cache_headroom_mb: int = 512
    rss_probe_timeout_s: float = 0.05
    dedup_lmdb_map_mb: int = 64
    conditional_cache_lmdb_mb: int = 16
    duckdb_wal_checkpoint_mb: int = 256
    http_cache_mb: int = 256
    http_cache_ttl_s: int = 86400
    http3_lru_max: int = 2048
    http3_concurrency_max: int = 5
    curl_host_session_max: int = 20
    curl_host_session_ttl_s: float = 300.0
    curl_prewarm_pool_size: int = 4
    circuit_breaker_max_domains: int = 512
    frontier_max_size: int = 1000
    evidence_ids_max: int = 500
    domain_failures_max: int = 1000
    domain_failure_cutoff_s: int = 86400
    duckdb_vacuum_threshold_mb: int = 2048


class MLXInference(Struct, frozen=True):
    """
    MLX inference limity — KV cache, batch, token boundy.

    Odvozeno z:
    - Model: Hermes-3-Llama-3.2-3B-4bit (~2GB RAM)
    - Context window: 8192 tokens
    - M1 Metal cache ceiling: 1 GiB
    - KV cache při 4bit quantization: ~0.75 GB
    """

    context_window: int = 8192
    default_max_tokens: int = 1024
    system_prompt_cache_kv: int = 512
    warmup_cache_tokens: int = 1000
    kv_cache_max: int = 8192
    kv_cache_min: int = 512
    batch_queue_max: int = 256
    batch_high_pressure_depth: int = 192
    batch_medium_pressure_depth: int = 64
    batch_max_size: int = 8
    batch_ema_alpha: float = 0.3
    batch_throughput_high: float = 10.0
    batch_throughput_low: float = 1.0
    flush_fast: float = 0.3
    flush_medium: float = 0.7
    flush_default: float = 2.0
    age_bump_interval: int = 3
    length_bin_short: int = 256
    length_bin_medium: int = 1024
    max_prompt_chars: int = 8192
    max_system_msg_chars: int = 8192
    prompt_long_threshold: int = 12000
    tokens_long_threshold: int = 2048
    # MODERN-36/37/38 Fix: Derive from UmaBudget SSOT (was 6.0/6.5/7.0)
    memory_warn_threshold: float = UmaBudget.THRESHOLD_WARN_GIB  # 5.938 GiB
    memory_critical_threshold: float = UmaBudget.THRESHOLD_CRITICAL_GIB  # 6.191 GiB
    memory_emergency_threshold: float = UmaBudget.THRESHOLD_EMERGENCY_GIB  # 6.25 GiB
    metal_pressure_bytes: int = 2 * 1024 * 1024 * 1024
    memory_ema_alpha: float = 0.15
    pid_kp: float = 0.5
    pid_ki: float = 0.05
    pid_kd: float = 0.1
    lora_kv_reduction_factor: int = 2


class ProtocolPorts(Struct, frozen=True):
    """
    Protocol-specific port numbers.

    Všechny porty jsou konfigurovatelné přes ENV vars.
    """

    tor_socks: int = 9050
    tor_control: int = 9051
    tor_max_circuit_dirtiness: int = 600
    i2p_socks: int = 7654
    i2p_sam: int = 7656
    i2p_http: int = 8888
    https: int = 443


class HTTPCodes(Struct, frozen=True):
    """
    HTTP status kódy pro retry/error policy.

    Duplikace PŘÍSĚ zakázána — všechny moduly používají HTTP.retryable
    """

    retryable: frozenset[int] = field(default_factory=lambda: frozenset({429, 502, 503, 504, 520}))
    escalation_trigger: frozenset[int] = field(default_factory=lambda: frozenset({401, 403}))
    success_min: int = 200
    success_max: int = 299
    redirect_min: int = 300
    redirect_max: int = 399


class SemanticRatios(Struct, frozen=True):
    """
    Empiricky odvozené plovoucí konstanty.

    Každá hodnota má poznámku ODKUD pochází (benchmark, historical fix, etc.)
    """

    windup_normal: float = 0.3
    windup_aggressive: float = 0.15
    windup_clamp_min: float = 30.0
    windup_clamp_max: float = 180.0
    windup_efficiency_critical: float = 0.4
    branch_remaining_ratio: float = 0.15
    branch_min_remaining: float = 2.0
    branch_max_remaining: float = 5.0
    dominance_ratio: float = 0.95
    memory_pressure_safe: float = 0.7
    memory_pressure_warn: float = 0.75
    aimd_decrease_factor: float = 0.75
    aimd_increase_factor: float = 0.25
    dedup_semantic_feed: float = 0.75
    dedup_semantic_default: float = 0.8
    dedup_semantic_strict: float = 0.85
    cover_traffic_rate: float = 0.05
    free_ram_min_mb: float = 256.0
    funnel_text_rate: float = 100.0
    windup_efficiency_acceptable: float = 0.7


class DuckDBStorage(Struct, frozen=True):
    """
    DuckDB-specific storage bounds.
    """

    batch_ingest_size: int = 2048
    pending_sync_markers_max: int = 10000
    replay_chunk_size: int = 100
    query_result_limit: int = 10000
    query_default_limit: int = 1000
    temporal_cutoff_days: int = 5
    temporal_cutoff_s: int = 5 * 86400
    dedup_semantic_feed: float = 0.75
    dedup_semantic_default: float = 0.8
    dedup_semantic_strict: float = 0.85
    embedding_dim: int = 256
    pending_futures_max: int = 256
    eval_granularity_tokens: int = 256
    clear_granularity_tokens: int = 256
    fallback_cache_bytes: int = 32 * 1024 * 1024
    min_body_cache_bytes: int = 256
    max_body_cache_bytes: int = 2 * 1024 * 1024
    dedup_lmdb_map_size: int = 64 * 1024 * 1024
    yield_per_min_floor: float = 0.001


def _make_lazy(cls):
    """Create a lazily-initialized singleton for the given dataclass."""
    instance = None

    def get():
        nonlocal instance
        if instance is None:
            instance = cls()
        return instance

    return get


NETWORK = _make_lazy(NetworkTimeouts)
M1_BOUNDS = _make_lazy(M1MemoryBounds)
MLX = _make_lazy(MLXInference)
PORTS = _make_lazy(ProtocolPorts)
HTTP = _make_lazy(HTTPCodes)
RATIOS = _make_lazy(SemanticRatios)
DUCKDB = _make_lazy(DuckDBStorage)
