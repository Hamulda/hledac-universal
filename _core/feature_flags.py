"""
core/feature_flags.py — Canonical Feature Flag Registry (ISSUE [SWARM]-010)

Single source of truth for all HLEDAC_ENABLE_* feature flags.




Solves the Feature Flag Sprawl problem:
- 70+ flags documented in CLAUDE.md but no runtime validation
- Code reading os.environ.get() directly, bypassing registry
- No dependency/conflict detection
- Deprecated flags silently accepted

Architecture:
- FeatureFlag enum: all known flags with metadata
- FeatureFlags class: singleton with typed getters, validation, deprecation warnings
- Integration with utils/flag_registry.py for Q1 compliance
- Startup validation wired into sprint_entrypoint.py

Usage:
    from hledac.universal._core.feature_flags import FeatureFlags, FeatureFlag

    # Check a flag
    if FeatureFlags.get(FeatureFlag.DSPY):
        ...

    # Validate at startup
    errors, warnings = FeatureFlags.validate()
    if errors:
        sys.exit(2)  # Config error per F350

    # List all flags
    for flag in FeatureFlags.list_all():
        print(f"{flag.name}: default={flag.default}, current={flag.value}")

Deprecation handling:
    HLEDAC_ENABLE_SYNTHESIS → HLEDAC_ENABLE_HERMES_SYNTHESIS
    HLEDAC_HTTP3 → HLEDAC_ENABLE_HTTPX_H3

Dependency rules (implies):
    DSPY → LLM
    HYPOTHESIS → LLM
    GRAPH_RAG → LLM + GRAPH_ANALYSIS
    GRAPH_PATHS → GRAPH_ANALYSIS
    BGP_PDNS → BGP
    FEDERATED_HYBRID → FEDERATED
    DEEP_RESEARCH → LLM
    LANCEDB_AUTO_TUNE → LANCEDB_QUANTIZE

Conflict pairs (mutual exclusion):
    CURL_CFFI ↔ HTTPX_H2
    NODRIVER ↔ HEAVY_BROWSER
    FEDERATED_HYBRID ↔ FEDERATED_P2P
    SYNTHESIS ↔ HERMES_SYNTHESIS (deprecated pair)

M1 8GB RAM budget:
    - Warning threshold: 5500MB
    - Fatal threshold: 7000MB
"""

from __future__ import annotations

__all__ = [
    "FeatureFlag",
    "FeatureFlags",
    "FlagInfo",
    "FlagCategory",
    "DeprecatedFlag",
    "FlagValidationError",
    "validate_sprint_flags",
    "get_flag_value",
]

import logging
import os
import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from functools import lru_cache
from typing import ClassVar

# Import existing registry for Q1 compliance
from hledac.universal.utils.flag_registry import (
    FLAG_REGISTRY,
    FlagSpec,
    validate_flag_combo as _validate_registry_combo,
)

logger = logging.getLogger(__name__)

# ============================================================================
# Enums
# ============================================================================


class FlagCategory(Enum):
    """10-group taxonomy matching Q1 probe requirements."""

    NETWORK = auto()
    BRAIN = auto()
    STORAGE = auto()
    DARK_SURFACE = auto()
    INTELLIGENCE_APIS = auto()
    FORENSICS = auto()
    STEALTH = auto()
    SYSTEM = auto()
    SECURITY = auto()  # Security/sandboxing flags
    PIPELINE = auto()  # Pipeline/processing flags


# ============================================================================
# Deprecated Flags
# ============================================================================


@dataclass(frozen=True, slots=True)
class DeprecatedFlag:
    """A deprecated flag with its replacement."""

    old_name: str
    new_name: str
    removed_in: str = ""  # e.g., "2025-Q4" or "" if unknown
    reason: str = ""


# Canonical deprecated flags map (old_name → DeprecatedFlag)
DEPRECATED_FLAGS: dict[str, DeprecatedFlag] = {
    # SYNTHESIS deprecated in favor of HERMES_SYNTHESIS (more explicit)
    "HLEDAC_ENABLE_SYNTHESIS": DeprecatedFlag(
        old_name="HLEDAC_ENABLE_SYNTHESIS",
        new_name="HLEDAC_ENABLE_HERMES_SYNTHESIS",
        reason="More explicit naming; both still work but SYNTHESIS emits a warning",
    ),
    # Legacy HTTP3 alias from F260
    "HLEDAC_HTTP3": DeprecatedFlag(
        old_name="HLEDAC_HTTP3",
        new_name="HLEDAC_ENABLE_HTTPX_H3",
        reason="Renamed for consistency with HLEDAC_ENABLE_* pattern",
    ),
    # Legacy deep research alias
    "HLEDAC_DEEP_RESEARCH": DeprecatedFlag(
        old_name="HLEDAC_DEEP_RESEARCH",
        new_name="HLEDAC_ENABLE_DEEP_RESEARCH",
        reason="Renamed for consistency with HLEDAC_ENABLE_* pattern",
    ),
    # Legacy LANCEDB flags (F264E cleanup)
    "HLEDAC_LANCEDB_AUTO_TUNE": DeprecatedFlag(
        old_name="HLEDAC_LANCEDB_AUTO_TUNE",
        new_name="HLEDAC_LANCEDB_AUTO_TUNE_ENABLED",
        reason="Renamed for boolean semantics clarity",
    ),
}


# ============================================================================
# FlagInfo dataclass (for list_all output)
# ============================================================================


@dataclass(slots=True)
class FlagInfo:
    """Runtime information about a single flag."""

    name: str
    flag: "FeatureFlag"
    category: FlagCategory
    default: bool
    value: bool
    description: str
    implies: tuple[str, ...]
    conflicts_with: tuple[str, ...]
    min_ram_mb: int
    is_deprecated: bool
    deprecated_replacement: str | None
    is_active: bool  # True if currently enabled


# ============================================================================
# FeatureFlag Enum
# ============================================================================


class FeatureFlag(Enum):
    """
    Canonical enum of all HLEDAC_ENABLE_* feature flags.

    Each enum member corresponds to an env var. Metadata is stored in
    _METADATA class dict.

    Add new flags here — this is the single source of truth.
    """

    # ─── Network Flags ────────────────────────────────────────────────────

    TOR = "HLEDAC_ENABLE_TOR"
    I2P = "HLEDAC_ENABLE_I2P"
    NYM = "HLEDAC_ENABLE_NYM"
    IPFS = "HLEDAC_ENABLE_IPFS"
    CURL_CFFI = "HLEDAC_ENABLE_CURL_CFFI"
    HTTPX_H2 = "HLEDAC_ENABLE_HTTPX_H2"
    HTTPX_H3 = "HLEDAC_ENABLE_HTTPX_H3"
    NW_CONNECTION = "HLEDAC_ENABLE_NW_CONNECTION"
    NW_QUIC = "HLEDAC_ENABLE_NW_QUIC"
    TRANSPORT_RACE = "HLEDAC_ENABLE_TRANSPORT_RACE"
    NODRIVER = "HLEDAC_ENABLE_NODRIVER"
    HEAVY_BROWSER = "HLEDAC_ENABLE_HEAVY_BROWSER"
    BANNER_GRAB = "HLEDAC_ENABLE_BANNER_GRAB"
    COMMONCRAWL = "HLEDAC_ENABLE_COMMONCRAWL"
    GOPHER = "HLEDAC_ENABLE_GOPHER"
    ALT_PROTOCOLS = "HLEDAC_ENABLE_ALT_PROTOCOLS"
    PROVIDERLESS_DISCOVERY = "HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY"
    ARTi = "HLEDAC_ENABLE_ARTI"
    DARK_PIVOTS = "HLEDAC_ENABLE_DARK_PIVOTS"

    # ─── Brain / LLM Flags ───────────────────────────────────────────────

    LLM = "HLEDAC_ENABLE_LLM"
    DSPY = "HLEDAC_ENABLE_DSPY"
    HYPOTHESIS = "HLEDAC_ENABLE_HYPOTHESIS"
    GRAPH_RAG = "HLEDAC_ENABLE_GRAPH_RAG"
    GRAPH_ANALYSIS = "HLEDAC_ENABLE_GRAPH_ANALYSIS"
    GRAPH_PATHS = "HLEDAC_ENABLE_GRAPH_PATHS"
    HERMES_SYNTHESIS = "HLEDAC_ENABLE_HERMES_SYNTHESIS"
    DEEP_RESEARCH = "HLEDAC_ENABLE_DEEP_RESEARCH"
    CONTENT_LAYER = "HLEDAC_ENABLE_CONTENT_LAYER"
    LAYERS = "HLEDAC_ENABLE_LAYERS"
    RESEARCH_LAYER = "HLEDAC_ENABLE_RESEARCH_LAYER"
    GLINER2 = "HLEDAC_ENABLE_GLINER2"
    MLX_OUTLINES = "HLEDAC_ENABLE_MLX_OUTLINES"
    ABSENCE_MINING = "HLEDAC_ENABLE_ABSENCE_MINING"
    AUTO_RE = "HLEDAC_ENABLE_AUTO_RE"

    # ─── Storage / Persistence Flags ─────────────────────────────────────

    TEMPORAL_STORE = "HLEDAC_ENABLE_TEMPORAL_STORE"
    LANCEDB_QUANTIZE = "HLEDAC_LANCEDB_QUANTIZE"
    LANCEDB_AUTO_TUNE = "HLEDAC_LANCEDB_AUTO_TUNE"
    GRAPH_STORE = "HLEDAC_ENABLE_GRAPH_STORE"
    CROSS_SPRINT_GATE = "HLEDAC_ENABLE_CROSS_SPRINT_GATE"
    ENTITY_CONFIRMATION = "HLEDAC_ENABLE_ENTITY_CONFIRMATION"
    SPRINT_DELTA_INDEX = "HLEDAC_ENABLE_SPRINT_DELTA_INDEX"
    DOMAIN_REPUTATION = "HLEDAC_DOMAIN_REPUTATION"
    PROXY_ROUTES = "HLEDAC_PROXY_ROUTES"
    ANTI_BOT_PROFILES = "HLEDAC_ANTI_BOT_PROFILES"
    SOURCE_RELIABILITY = "HLEDAC_ENABLE_SOURCE_RELIABILITY"
    TIMELINE_SPLICER = "HLEDAC_ENABLE_TIMELINE_SPLICER"
    IOC_TEMPORAL_PROVENANCE = "HLEDAC_ENABLE_IOC_TEMPORAL_PROVENANCE"
    WARC_ENABLED = "HLEDAC_WARC_ENABLED"
    ARROW_INGEST = "HLEDAC_ARROW_INGEST"
    ENTROPY_FEEDBACK = "HLEDAC_ENABLE_ENTROPY_FEEDBACK"
    MICRO_SPRINT_CONTRADICTION = "HLEDAC_ENABLE_MICRO_SPRINT_CONTRADICTION"
    CONTRADICTION_FEEDBACK = "HLEDAC_ENABLE_CONTRADICTION_FEEDBACK"
    CONSISTENCY_VERIFIER = "HLEDAC_ENABLE_CONSISTENCY_VERIFIER"
    DASHBOARD = "HLEDAC_ENABLE_DASHBOARD"
    CROSS_LANE_TEMPORAL_CORRELATION = "HLEDAC_ENABLE_CROSS_LANE_TEMPORAL_CORRELATION"

    # ─── Intelligence API Flags ─────────────────────────────────────────

    BGP = "HLEDAC_ENABLE_BGP"
    BGP_PDNS = "HLEDAC_ENABLE_BGP_PDNS"
    ACADEMIC = "HLEDAC_ENABLE_ACADEMIC"
    LEAKSENTINEL = "HLEDAC_ENABLE_LEAKSENTINEL"
    CENSYS = "HLEDAC_ENABLE_CENSYS"
    SHODAN = "HLEDAC_ENABLE_SHODAN"
    GREYNOISE = "HLEDAC_ENABLE_GREYNOISE"
    TI_FEEDS = "HLEDAC_ENABLE_TI_FEEDS"
    FEDERATED = "HLEDAC_ENABLE_FEDERATED"
    FEDERATED_HYBRID = "HLEDAC_ENABLE_FEDERATED_HYBRID"
    FEDERATED_P2P = "HLEDAC_ENABLE_FEDERATED_P2P"
    SOCIAL = "HLEDAC_ENABLE_SOCIAL"
    FEDIVERSE = "HLEDAC_ENABLE_FEDIVERSE"
    DHT = "HLEDAC_ENABLE_DHT"
    BLOCKCHAIN_ANALYZER = "HLEDAC_ENABLE_BLOCKCHAIN_ANALYZER"

    # ─── Forensics Flags ─────────────────────────────────────────────────

    STEGANOGRAPHY = "HLEDAC_ENABLE_STEGANOGRAPHY"
    STEGDETECT_SIGNED = "HLEDAC_ENABLE_STEGDETECT_SIGNED"
    DIGITAL_GHOST = "HLEDAC_ENABLE_DIGITAL_GHOST"
    IMAGE_OSINT = "HLEDAC_ENABLE_IMAGE_OSINT"
    BLOCKCHAIN_FORENSICS = "HLEDAC_ENABLE_BLOCKCHAIN_ANALYZER"
    NETWORK_RECON = "HLEDAC_ENABLE_NETWORK_RECON"
    CAPTCHA_DETECTION = "HLEDAC_ENABLE_CAPTCHA_DETECTION"
    CAPTCHA_LOCAL = "HLEDAC_ENABLE_CAPTCHA_LOCAL"
    NATIVE_EXTRACTION = "HLEDAC_ENABLE_NATIVE_EXTRACTION"

    # ─── Stealth / Privacy Flags ─────────────────────────────────────────

    STEALTH_LAYER = "HLEDAC_ENABLE_STEALTH_LAYER"
    PRIVACY_LAYER = "HLEDAC_ENABLE_PRIVACY_LAYER"
    ZKP = "HLEDAC_ENABLE_ZKP"
    ZERO_ATTRIBUTION = "HLEDAC_ENABLE_ZERO_ATTRIBUTION"
    NEURO_CRYPTO = "HLEDAC_EXPERIMENTAL_NEURO_CRYPTO"
    PIVOT_STAGGER_MS = "HLEDAC_PIVOT_STAGGER_MS"
    SHODAN_JITTER = "HLEDAC_SHODAN_JITTER_SIGMA_S"
    GREYNOISE_JITTER = "HLEDAC_GREYNOISE_JITTER_SIGMA_S"
    CENSYS_JITTER = "HLEDAC_CENSYS_JITTER_SIGMA_S"
    RACE_STAGGER_MS = "HLEDAC_RACE_STAGGER_MS"
    PIVOT_EXEC_JITTER = "HLEDAC_PIVOT_EXEC_JITTER_S"

    # ─── System / Runtime Flags ──────────────────────────────────────────

    CONTENT_HASHER = "HLEDAC_CONTENT_HASHER"
    BENCHMARK = "HLEDAC_BENCHMARK"
    OFFLINE = "HLEDAC_OFFLINE"
    RL_SKIP_RAM_GATE = "HLEDAC_RL_SKIP_RAM_GATE"
    DISABLE_GC_FREEZE = "HLEDAC_DISABLE_GC_FREEZE"
    DISABLE_RL = "HLEDAC_DISABLE_RL"
    TRACEMALLOC = "HLEDAC_TRACEMALLOC"
    RAYON_ELASTIC = "HLEDAC_ENABLE_RAYON_ELASTIC"
    SUBINTERPRETERS = "HLEDAC_ENABLE_SUBINTERPRETERS"
    DNS = "HLEDAC_ENABLE_DNS"
    DNS_RUST = "HLEDAC_ENABLE_DNS_RUST"
    SWARM_DAG = "HLEDAC_ENABLE_SWARM_DAG"
    HTTP_CACHE = "HLEDAC_HTTP_CACHE"
    COVER_TRAFFIC_RATE = "HLEDAC_COVER_TRAFFIC_RATE"
    BLITZ_FETCH = "HLEDAC_BLITZ_FETCH"
    RATE_LIMIT_RPS = "HLEDAC_RATE_LIMIT_RPS"
    H2_WEBKIT_PRESET = "HLEDAC_H2_WEBKIT_PRESET"
    CURL_CFFI_PREWARM = "HLEDAC_CURL_CFFI_PREWARM"
    CONDITIONAL_CACHE = "HLEDAC_CONDITIONAL_CACHE"
    DISCOVERY_REPLAY = "HLEDAC_DISCOVERY_REPLAY"
    REPLAY_STRICT = "HLEDAC_REPLAY_STRICT"
    WISP = "HLEDAC_ENABLE_WISP"
    METAL_HASHCRACK = "HLEDAC_ENABLE_METAL_HASHCRACK"
    METAL_HNSW = "HLEDAC_ENABLE_METAL_HNSW"
    WHISPER = "HLEDAC_ENABLE_WHISPER"
    METAL_GEMM = "HLEDAC_ENABLE_METAL_GEMM"

    # ─── MLX / Apple Silicon Flags ───────────────────────────────────────

    MLX = "HLEDAC_MLX"
    MLX_PREWARM = "HLEDAC_MLX_PREWARM"
    ANE_INFERENCE = "HLEDAC_ENABLE_ANE_INFERENCE"
    DISABLE_ANE = "HLEDAC_DISABLE_ANE"
    KV_CACHE = "HLEDAC_ENABLE_KV_CACHE"

    # ─── Brain / Processing Flags ────────────────────────────────────────

    COGNITIVE_TARPIT = "HLEDAC_ENABLE_COGNITIVE_TARPIT"
    POS_TAGGING = "HLEDAC_ENABLE_POS_TAGGING"
    TRIAGE_DISABLED = "HLEDAC_TRIAGE_DISABLED"
    TRIAGE_TIER2 = "HLEDAC_TRIAGE_TIER2"
    DISABLE_SPEC_DECODE = "HLEDAC_DISABLE_SPEC_DECODE"
    ENABLE_SPEC_DECODE = "HLEDAC_ENABLE_SPEC_DECODE"

    # ─── Knowledge / RAG Flags ──────────────────────────────────────────

    HOT_EDGES = "HLEDAC_HOT_EDGES"
    HOT_EDGES_COMPRESS = "HLEDAC_HOT_EDGES_COMPRESS"
    HOT_EDGES_L1_FLUSH = "HLEDAC_HOT_EDGES_L1_FLUSH"
    HOT_EDGES_MAP_SIZE_MB = "HLEDAC_HOT_EDGES_MAP_SIZE_MB"
    RAG_ULTRA_CONTEXT = "HLEDAC_RAG_ULTRA_CONTEXT"
    RAG_SECURE_ENCLAVE = "HLEDAC_RAG_SECURE_ENCLAVE"
    RAG_SPR_COMPRESSION = "HLEDAC_RAG_SPR_COMPRESSION"
    RAG_HYBRID_RETRIEVAL = "HLEDAC_RAG_HYBRID_RETRIEVAL"
    RAG_COMPRESSION_THRESHOLD = "HLEDAC_RAG_COMPRESSION_THRESHOLD"
    RAG_MAX_TOKENS = "HLEDAC_RAG_MAX_TOKENS"
    RAG_DENSE_WEIGHT = "HLEDAC_RAG_DENSE_WEIGHT"
    RAG_SPARSE_WEIGHT = "HLEDAC_RAG_SPARSE_WEIGHT"
    RAG_BM25_K1 = "HLEDAC_RAG_BM25_K1"
    RAG_BM25_B = "HLEDAC_RAG_BM25_B"
    RAG_CHUNK_SIZE = "HLEDAC_RAG_CHUNK_SIZE"
    RAG_CHUNK_OVERLAP = "HLEDAC_RAG_CHUNK_OVERLAP"
    RAG_USE_HNSW = "HLEDAC_RAG_USE_HNSW"
    RAG_HNSW_DIM = "HLEDAC_RAG_HNSW_DIM"
    RAG_HNSW_MAX_ELEMENTS = "HLEDAC_RAG_HNSW_MAX_ELEMENTS"
    RAG_HNSW_M = "HLEDAC_RAG_HNSW_M"
    RAG_HNSW_EF_CONSTRUCTION = "HLEDAC_RAG_HNSW_EF_CONSTRUCTION"
    RAG_HNSW_EF_SEARCH = "HLEDAC_RAG_HNSW_EF_SEARCH"
    RAG_HNSW_INDEX_PATH = "HLEDAC_RAG_HNSW_INDEX_PATH"
    RAG_HNSW_SPACE = "HLEDAC_RAG_HNSW_SPACE"
    DISABLE_RUST_FULLTEXT = "HLEDAC_DISABLE_RUST_FULLTEXT"
    VECTOR_BACKEND = "HLEDAC_VECTOR_BACKEND"

    # ─── Network / Transport Flags ───────────────────────────────────────

    ENABLE_QUIC = "HLEDAC_ENABLE_QUIC"

    # ─── Export / Security Flags ─────────────────────────────────────────

    ENABLE_PQ_EXPORT = "HLEDAC_ENABLE_PQ_EXPORT"
    VAULT_EXPORT = "HLEDAC_VAULT_EXPORT"

    # ─── Evidence / Logging Flags ───────────────────────────────────────

    ARROW_EVIDENCE = "HLEDAC_ARROW_EVIDENCE"
    EVIDENCE_DUCKDB = "HLEDAC_EVIDENCE_DUCKDB"
    CLAIMS_EXTRACTION = "HLEDAC_ENABLE_CLAIMS_EXTRACTION"

    # ─── DuckDB / Storage Config Flags ──────────────────────────────────

    DUCKDB_INPROCESS = "HLEDAC_DUCKDB_INPROCESS"
    DUCKDB_THREADS = "HLEDAC_DUCKDB_THREADS"
    RAMDISK_AUTO_CREATE = "HLEDAC_RAMDISK_AUTO_CREATE"
    RAMDISK = "HLEDAC_RAMDISK"
    SPRINT_STORE = "HLEDAC_SPRINT_STORE"
    DEDUP_DISK = "HLEDAC_DEDUP_DISK"
    DEDUP_SIZE_MB = "HLEDAC_DEDUP_SIZE_MB"

    # ─── Intel / Analytics Flags ─────────────────────────────────────────

    GRAPH_STORE_RAG = "HLEDAC_ENABLE_GRAPH_STORE_RAG"
    INSIGHT_ENGINE = "HLEDAC_ENABLE_INSIGHT_ENGINE"
    ADVERSARIAL_VERIFIER = "HLEDAC_ENABLE_ADVERSARIAL_VERIFIER"
    DEMPSTER_SHAFER = "HLEDAC_ENABLE_DEMPSTER_SHAFER"
    EVIDENCE_NETWORK = "HLEDAC_ENABLE_EVIDENCE_NETWORK"

    # ─── Misc / Debug Flags ──────────────────────────────────────────────

    DEBUG_JA3 = "HLEDAC_DEBUG_JA3"
    FORCE_PYTHON = "HLEDAC_FORCE_PYTHON"
    FORCE_RUST = "HLEDAC_FORCE_RUST"
    DISABLE_WHISPER = "HLEDAC_DISABLE_WHISPER"
    BLITZ_TRIAGE = "HLEDAC_ENABLE_BLITZ_TRIAGE"
    NETWORK_ANALYTICS = "HLEDAC_ENABLE_NETWORK_ANALYTICS"

    # ─── Browser / Stealth Config ─────────────────────────────────────────

    BROWSER_MEM_THRESHOLD_GIB = "HLEDAC_BROWSER_MEM_THRESHOLD_GIB"

    # ─── Security / Sandboxing Flags ──────────────────────────────────────

    MACH_REMAP = "HLEDAC_ENABLE_MACH_REMAP"
    DOC_SANDBOX = "HLEDAC_ENABLE_DOC_SANDBOX"
    EPHEMERAL_WIPE = "HLEDAC_ENABLE_EPHEMERAL_WIPE"
    NATIVE_EXTRACTION = "HLEDAC_ENABLE_NATIVE_EXTRACTION"
    REMOTE_DEBUG_DISABLE = "HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED"

    # ─── Cognitive / Runtime Flags ─────────────────────────────────────────

    COGNITIVE_SATURATION = "HLEDAC_ENABLE_COGNITIVE_SATURATION"
    AUTO_RE = "HLEDAC_ENABLE_AUTO_RE"
    SUBINTERPRETERS = "HLEDAC_ENABLE_SUBINTERPRETERS"

    # ─── Async / Logging Flags ─────────────────────────────────────────────

    ASYNC_LOG = "HLEDAC_ASYNC_LOG"

    # ─── Deobfuscation / Pipeline ─────────────────────────────────────────

    ENABLE_DEOBFUSCATE = "HLEDAC_ENABLE_DEOBFUSCATE"

    # ─── Deep Research Config ─────────────────────────────────────────────

    DEEP_RESEARCH = "HLEDAC_DEEP_RESEARCH"

    # ─── Async / Logging Config ───────────────────────────────────────────

    ASYNC_LOG_DROP_OLDEST = "HLEDAC_ASYNC_LOG_DROP_OLDEST"
    MAX_PENDING_OPS = "HLEDAC_MAX_PENDING_OPS"

    # ─── IPFS Config ──────────────────────────────────────────────────────

    IPFS_GATEWAY_URL = "HLEDAC_IPFS_GATEWAY_URL"

    # ─── Memory / Budget Config ────────────────────────────────────────────

    PEAK_BUDGET_GIB = "HLEDAC_PEAK_BUDGET_GIB"

    # ─── Deduplication Config ─────────────────────────────────────────────

    DEDUP_DISK = "HLEDAC_DEDUP_DISK"
    DEDUP_SIZE_MB = "HLEDAC_DEDUP_SIZE_MB"
    DEDUP_DIR = "HLEDAC_DEDUP_DIR"
    DEDUP_MAX_NGRAMS = "HLEDAC_DEDUP_MAX_NGRAMS"

    # ─── Captcha Config ───────────────────────────────────────────────────

    ENABLE_CAPTCHA = "HLEDAC_ENABLE_CAPTCHA"

    # ─── LanceDB Quantization Config ─────────────────────────────────────

    LANCEDB_IVFPQ_NUM_SUB_VECTORS = "HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS"
    LANCEDB_IVFPQ_NUM_PARTITIONS = "HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS"

    # ─── Storage Paths ────────────────────────────────────────────────────

    DUCKDB_STORE = "HLEDAC_DUCKDB_STORE"
    LANCEDB_STORE = "HLEDAC_LANCEDB_STORE"
    LMDB_STORE = "HLEDAC_LMDB_STORE"
    SPRINT_STORE = "HLEDAC_SPRINT_STORE"

    # ─── Whois Config ─────────────────────────────────────────────────────

    WHOIS_API = "HLEDAC_WHOIS_API"
    WHOIS_API_KEY = "HLEDAC_WHOIS_API_KEY"

    # ─── Network / Retry Config ───────────────────────────────────────────

    MAX_RETRIES = "HLEDAC_MAX_RETRIES"
    FETCH_TIMEOUT = "HLEDAC_FETCH_TIMEOUT"

    # ─── Telemetry / Observability ───────────────────────────────────────

    OTEL_SAMPLE_RATIO = "HLEDAC_OTEL_SAMPLE_RATIO"
    OTEL_SLOW_SPAN_MS = "HLEDAC_OTEL_SLOW_SPAN_MS"

    # ─── Acquisition / RL Config ─────────────────────────────────────────

    ACQUISITION_PROFILE = "HLEDAC_ACQUISITION_PROFILE"
    RL_TRAIN = "HLEDAC_RL_TRAIN"


# ============================================================================
# Flag Metadata Class Dict
# ============================================================================


@lru_cache(maxsize=1)
def _build_metadata() -> dict[FeatureFlag, dict]:
    """
    Build flag metadata from existing FLAG_REGISTRY + additions.

    This merges:
    1. FLAG_REGISTRY specs (authoritative for implies/conflicts/ram)
    2. Hardcoded metadata for flags not yet in registry
    3. Category mappings
    """
    metadata: dict[FeatureFlag, dict] = {}

    # Category mapping (flag name → category)
    category_map: dict[str, FlagCategory] = {
        # Network
        "HLEDAC_ENABLE_TOR": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_I2P": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_NYM": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_IPFS": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_CURL_CFFI": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_HTTPX_H2": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_HTTPX_H3": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_NW_CONNECTION": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_NW_QUIC": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_TRANSPORT_RACE": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_NODRIVER": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_HEAVY_BROWSER": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_BANNER_GRAB": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_COMMONCRAWL": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_GOPHER": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_ALT_PROTOCOLS": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_ARTI": FlagCategory.NETWORK,
        "HLEDAC_ENABLE_DARK_PIVOTS": FlagCategory.DARK_SURFACE,
        # Brain
        "HLEDAC_ENABLE_LLM": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_DSPY": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_HYPOTHESIS": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_GRAPH_RAG": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_HERMES_SYNTHESIS": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_DEEP_RESEARCH": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_CONTENT_LAYER": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_LAYERS": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_RESEARCH_LAYER": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_GLINER2": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_MLX_OUTLINES": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_ABSENCE_MINING": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_AUTO_RE": FlagCategory.BRAIN,
        # Storage
        "HLEDAC_ENABLE_TEMPORAL_STORE": FlagCategory.STORAGE,
        "HLEDAC_LANCEDB_QUANTIZE": FlagCategory.STORAGE,
        "HLEDAC_LANCEDB_AUTO_TUNE": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_GRAPH_STORE": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_CROSS_SPRINT_GATE": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_ENTITY_CONFIRMATION": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_SPRINT_DELTA_INDEX": FlagCategory.STORAGE,
        "HLEDAC_DOMAIN_REPUTATION": FlagCategory.STORAGE,
        "HLEDAC_PROXY_ROUTES": FlagCategory.STORAGE,
        "HLEDAC_ANTI_BOT_PROFILES": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_SOURCE_RELIABILITY": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_TIMELINE_SPLICER": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_IOC_TEMPORAL_PROVENANCE": FlagCategory.STORAGE,
        "HLEDAC_WARC_ENABLED": FlagCategory.STORAGE,
        "HLEDAC_ARROW_INGEST": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_ENTROPY_FEEDBACK": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_MICRO_SPRINT_CONTRADICTION": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_CONTRADICTION_FEEDBACK": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_CONSISTENCY_VERIFIER": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_DASHBOARD": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_CROSS_LANE_TEMPORAL_CORRELATION": FlagCategory.STORAGE,
        # Storage (graph)
        "HLEDAC_ENABLE_GRAPH_ANALYSIS": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_GRAPH_PATHS": FlagCategory.STORAGE,
        # Intelligence APIs
        "HLEDAC_ENABLE_BGP": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_BGP_PDNS": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_ACADEMIC": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_LEAKSENTINEL": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_CENSYS": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_SHODAN": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_GREYNOISE": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_TI_FEEDS": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_FEDERATED": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_FEDERATED_HYBRID": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_FEDERATED_P2P": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_SOCIAL": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_FEDIVERSE": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_ENABLE_DHT": FlagCategory.DARK_SURFACE,
        "HLEDAC_ENABLE_BLOCKCHAIN_ANALYZER": FlagCategory.FORENSICS,
        # Forensics
        "HLEDAC_ENABLE_STEGANOGRAPHY": FlagCategory.FORENSICS,
        "HLEDAC_ENABLE_STEGDETECT_SIGNED": FlagCategory.FORENSICS,
        "HLEDAC_ENABLE_DIGITAL_GHOST": FlagCategory.FORENSICS,
        "HLEDAC_ENABLE_IMAGE_OSINT": FlagCategory.FORENSICS,
        "HLEDAC_ENABLE_NETWORK_RECON": FlagCategory.FORENSICS,
        "HLEDAC_ENABLE_CAPTCHA_DETECTION": FlagCategory.FORENSICS,
        "HLEDAC_ENABLE_CAPTCHA_LOCAL": FlagCategory.FORENSICS,
        "HLEDAC_ENABLE_NATIVE_EXTRACTION": FlagCategory.FORENSICS,
        "HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED": FlagCategory.SECURITY,
        # Stealth
        "HLEDAC_ENABLE_STEALTH_LAYER": FlagCategory.STEALTH,
        "HLEDAC_ENABLE_PRIVACY_LAYER": FlagCategory.STEALTH,
        "HLEDAC_ENABLE_ZKP": FlagCategory.STEALTH,
        "HLEDAC_ENABLE_ZERO_ATTRIBUTION": FlagCategory.STEALTH,
        "HLEDAC_EXPERIMENTAL_NEURO_CRYPTO": FlagCategory.STEALTH,
        # System
        "HLEDAC_CONTENT_HASHER": FlagCategory.SYSTEM,
        "HLEDAC_BENCHMARK": FlagCategory.SYSTEM,
        "HLEDAC_OFFLINE": FlagCategory.SYSTEM,
        "HLEDAC_RL_SKIP_RAM_GATE": FlagCategory.SYSTEM,
        "HLEDAC_DISABLE_GC_FREEZE": FlagCategory.SYSTEM,
        "HLEDAC_DISABLE_RL": FlagCategory.SYSTEM,
        "HLEDAC_TRACEMALLOC": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_RAYON_ELASTIC": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_SUBINTERPRETERS": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_DNS": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_DNS_RUST": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_SWARM_DAG": FlagCategory.SYSTEM,
        "HLEDAC_HTTP_CACHE": FlagCategory.SYSTEM,
        "HLEDAC_COVER_TRAFFIC_RATE": FlagCategory.SYSTEM,
        "HLEDAC_BLITZ_FETCH": FlagCategory.SYSTEM,
        "HLEDAC_RATE_LIMIT_RPS": FlagCategory.SYSTEM,
        "HLEDAC_H2_WEBKIT_PRESET": FlagCategory.SYSTEM,
        "HLEDAC_CURL_CFFI_PREWARM": FlagCategory.SYSTEM,
        "HLEDAC_CONDITIONAL_CACHE": FlagCategory.SYSTEM,
        "HLEDAC_DISCOVERY_REPLAY": FlagCategory.SYSTEM,
        "HLEDAC_REPLAY_STRICT": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_WISP": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_METAL_HASHCRACK": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_METAL_HNSW": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_WHISPER": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_METAL_GEMM": FlagCategory.SYSTEM,
        # DuckDB / Storage Config
        "HLEDAC_DUCKDB_INPROCESS": FlagCategory.STORAGE,
        "HLEDAC_DUCKDB_THREADS": FlagCategory.STORAGE,
        "HLEDAC_RAMDISK_AUTO_CREATE": FlagCategory.STORAGE,
        "HLEDAC_RAMDISK": FlagCategory.STORAGE,
        "HLEDAC_SPRINT_STORE": FlagCategory.STORAGE,
        "HLEDAC_DEDUP_DISK": FlagCategory.STORAGE,
        "HLEDAC_DEDUP_SIZE_MB": FlagCategory.STORAGE,
        # Intel / Analytics
        "HLEDAC_ENABLE_GRAPH_STORE_RAG": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_INSIGHT_ENGINE": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_ADVERSARIAL_VERIFIER": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_DEMPSTER_SHAFER": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_EVIDENCE_NETWORK": FlagCategory.BRAIN,
        # Debug
        "HLEDAC_DEBUG_JA3": FlagCategory.SYSTEM,
        "HLEDAC_FORCE_PYTHON": FlagCategory.SYSTEM,
        "HLEDAC_FORCE_RUST": FlagCategory.SYSTEM,
        "HLEDAC_DISABLE_WHISPER": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_BLITZ_TRIAGE": FlagCategory.SYSTEM,
        "HLEDAC_ENABLE_NETWORK_ANALYTICS": FlagCategory.SYSTEM,
        # Config flags (numeric/non-boolean)
        "HLEDAC_PIVOT_STAGGER_MS": FlagCategory.STEALTH,
        "HLEDAC_SHODAN_JITTER_SIGMA_S": FlagCategory.STEALTH,
        "HLEDAC_GREYNOISE_JITTER_SIGMA_S": FlagCategory.STEALTH,
        "HLEDAC_CENSYS_JITTER_SIGMA_S": FlagCategory.STEALTH,
        "HLEDAC_RACE_STAGGER_MS": FlagCategory.STEALTH,
        "HLEDAC_PIVOT_EXEC_JITTER_S": FlagCategory.STEALTH,
        # MLX / Apple Silicon
        "HLEDAC_MLX": FlagCategory.BRAIN,
        "HLEDAC_MLX_PREWARM": FlagCategory.BRAIN,
        # Brain / Processing
        "HLEDAC_ENABLE_COGNITIVE_TARPIT": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_POS_TAGGING": FlagCategory.BRAIN,
        "HLEDAC_TRIAGE_DISABLED": FlagCategory.BRAIN,
        "HLEDAC_TRIAGE_TIER2": FlagCategory.BRAIN,
        "HLEDAC_DISABLE_SPEC_DECODE": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_SPEC_DECODE": FlagCategory.BRAIN,
        # Knowledge / RAG
        "HLEDAC_HOT_EDGES": FlagCategory.STORAGE,
        "HLEDAC_HOT_EDGES_COMPRESS": FlagCategory.STORAGE,
        "HLEDAC_HOT_EDGES_L1_FLUSH": FlagCategory.STORAGE,
        "HLEDAC_HOT_EDGES_MAP_SIZE_MB": FlagCategory.STORAGE,
        "HLEDAC_RAG_ULTRA_CONTEXT": FlagCategory.BRAIN,
        "HLEDAC_RAG_SECURE_ENCLAVE": FlagCategory.BRAIN,
        "HLEDAC_RAG_SPR_COMPRESSION": FlagCategory.BRAIN,
        "HLEDAC_RAG_HYBRID_RETRIEVAL": FlagCategory.BRAIN,
        "HLEDAC_RAG_COMPRESSION_THRESHOLD": FlagCategory.BRAIN,
        "HLEDAC_RAG_MAX_TOKENS": FlagCategory.BRAIN,
        "HLEDAC_RAG_DENSE_WEIGHT": FlagCategory.BRAIN,
        "HLEDAC_RAG_SPARSE_WEIGHT": FlagCategory.BRAIN,
        "HLEDAC_RAG_BM25_K1": FlagCategory.BRAIN,
        "HLEDAC_RAG_BM25_B": FlagCategory.BRAIN,
        "HLEDAC_RAG_CHUNK_SIZE": FlagCategory.BRAIN,
        "HLEDAC_RAG_CHUNK_OVERLAP": FlagCategory.BRAIN,
        "HLEDAC_RAG_USE_HNSW": FlagCategory.BRAIN,
        "HLEDAC_RAG_HNSW_DIM": FlagCategory.BRAIN,
        "HLEDAC_RAG_HNSW_MAX_ELEMENTS": FlagCategory.BRAIN,
        "HLEDAC_RAG_HNSW_M": FlagCategory.BRAIN,
        "HLEDAC_RAG_HNSW_EF_CONSTRUCTION": FlagCategory.BRAIN,
        "HLEDAC_RAG_HNSW_EF_SEARCH": FlagCategory.BRAIN,
        "HLEDAC_RAG_HNSW_INDEX_PATH": FlagCategory.BRAIN,
        "HLEDAC_RAG_HNSW_SPACE": FlagCategory.BRAIN,
        "HLEDAC_DISABLE_RUST_FULLTEXT": FlagCategory.BRAIN,
        "HLEDAC_VECTOR_BACKEND": FlagCategory.STORAGE,
        # Network / Transport
        "HLEDAC_ENABLE_QUIC": FlagCategory.NETWORK,
        # Export / Security
        "HLEDAC_ENABLE_PQ_EXPORT": FlagCategory.STORAGE,
        "HLEDAC_VAULT_EXPORT": FlagCategory.STORAGE,
        # Evidence / Logging
        "HLEDAC_ARROW_EVIDENCE": FlagCategory.STORAGE,
        "HLEDAC_EVIDENCE_DUCKDB": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_CLAIMS_EXTRACTION": FlagCategory.STORAGE,
        # Browser / Stealth
        "HLEDAC_BROWSER_MEM_THRESHOLD_GIB": FlagCategory.STEALTH,
        # Deobfuscation / Pipeline
        "HLEDAC_ENABLE_DEOBFUSCATE": FlagCategory.FORENSICS,
        # Deep Research
        "HLEDAC_DEEP_RESEARCH": FlagCategory.BRAIN,
        # Async / Logging
        "HLEDAC_ASYNC_LOG_DROP_OLDEST": FlagCategory.SYSTEM,
        "HLEDAC_MAX_PENDING_OPS": FlagCategory.SYSTEM,
        # IPFS
        "HLEDAC_IPFS_GATEWAY_URL": FlagCategory.NETWORK,
        # Memory / Budget
        "HLEDAC_PEAK_BUDGET_GIB": FlagCategory.SYSTEM,
        # Deduplication
        "HLEDAC_DEDUP_DISK": FlagCategory.STORAGE,
        "HLEDAC_DEDUP_SIZE_MB": FlagCategory.STORAGE,
        "HLEDAC_DEDUP_DIR": FlagCategory.STORAGE,
        "HLEDAC_DEDUP_MAX_NGRAMS": FlagCategory.STORAGE,
        # Captcha
        "HLEDAC_ENABLE_CAPTCHA": FlagCategory.FORENSICS,
        # LanceDB Quantization
        "HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS": FlagCategory.STORAGE,
        "HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS": FlagCategory.STORAGE,
        # Storage Paths
        "HLEDAC_DUCKDB_STORE": FlagCategory.STORAGE,
        "HLEDAC_LANCEDB_STORE": FlagCategory.STORAGE,
        "HLEDAC_LMDB_STORE": FlagCategory.STORAGE,
        "HLEDAC_RAMDISK": FlagCategory.STORAGE,
        # Whois
        "HLEDAC_WHOIS_API": FlagCategory.INTELLIGENCE_APIS,
        "HLEDAC_WHOIS_API_KEY": FlagCategory.INTELLIGENCE_APIS,
        # Network / Retry
        "HLEDAC_MAX_RETRIES": FlagCategory.NETWORK,
        "HLEDAC_FETCH_TIMEOUT": FlagCategory.NETWORK,
        # Telemetry
        "HLEDAC_OTEL_SAMPLE_RATIO": FlagCategory.SYSTEM,
        "HLEDAC_OTEL_SLOW_SPAN_MS": FlagCategory.SYSTEM,
        # Acquisition / RL
        "HLEDAC_ACQUISITION_PROFILE": FlagCategory.SYSTEM,
        "HLEDAC_RL_TRAIN": FlagCategory.SYSTEM,
        "HLEDAC_RAMDISK_AUTO_CREATE": FlagCategory.STORAGE,
        # Graph Store
        "HLEDAC_ENABLE_GRAPH_ANALYSIS": FlagCategory.STORAGE,
        "HLEDAC_ENABLE_GRAPH_PATHS": FlagCategory.STORAGE,
        # Sprint
        "HLEDAC_ENABLE_TIMELINE_SPLICER": FlagCategory.STORAGE,
        # Security / Sandboxing
        "HLEDAC_ENABLE_MACH_REMAP": FlagCategory.SECURITY,
        "HLEDAC_ENABLE_DOC_SANDBOX": FlagCategory.SECURITY,
        "HLEDAC_ENABLE_EPHEMERAL_WIPE": FlagCategory.SECURITY,
        "HLEDAC_ENABLE_NATIVE_EXTRACTION": FlagCategory.FORENSICS,
        "HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED": FlagCategory.SECURITY,
        # Cognitive / Runtime
        "HLEDAC_ENABLE_COGNITIVE_SATURATION": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_AUTO_RE": FlagCategory.BRAIN,
        "HLEDAC_ENABLE_SUBINTERPRETERS": FlagCategory.SYSTEM,
        # Async / Logging
        "HLEDAC_ASYNC_LOG": FlagCategory.SYSTEM,
        # Browser
        "HLEDAC_BROWSER_MEM_THRESHOLD_GIB": FlagCategory.SYSTEM,
        # Deobfuscation
        "HLEDAC_ENABLE_DEOBFUSCATE": FlagCategory.PIPELINE,
    }

    # Default values (boolean flags default to False unless specified)
    defaults: dict[str, bool] = {
        # ON by default
        "HLEDAC_ENABLE_ABSENCE_MINING": True,
        "HLEDAC_DUCKDB_INPROCESS": True,
        "HLEDAC_DUCKDB_THREADS": True,  # Actually int, but treated as bool for enable
        "HLEDAC_ENABLE_GLINER2": True,
        "HLEDAC_H2_WEBKIT_PRESET": True,
        "HLEDAC_ENABLE_SPRINT_DELTA_INDEX": True,
        "HLEDAC_ENABLE_STEGDETECT_SIGNED": True,
        "HLEDAC_ENABLE_RAYON_ELASTIC": True,
        "HLEDAC_ENABLE_NW_QUIC": True,
        "HLEDAC_ENABLE_NW_CONNECTION": True,
        "HLEDAC_ENABLE_TRANSPORT_RACE": True,
        "HLEDAC_ENABLE_WHISPER": True,
        "HLEDAC_ENABLE_ENTROPY_FEEDBACK": True,
        "HLEDAC_ENABLE_MICRO_SPRINT_CONTRADICTION": True,
        "HLEDAC_ENABLE_CONTRADICTION_FEEDBACK": True,
        "HLEDAC_ENABLE_CONSISTENCY_VERIFIER": True,
        "HLEDAC_ENABLE_CROSS_SPRINT_GATE": True,
        "HLEDAC_ENABLE_ENTITY_CONFIRMATION": True,
        "HLEDAC_ENABLE_SOURCE_RELIABILITY": True,
        "HLEDAC_ENABLE_IOC_TEMPORAL_PROVENANCE": True,
        "HLEDAC_DOMAIN_REPUTATION": True,
        "HLEDAC_PROXY_ROUTES": True,
        "HLEDAC_ANTI_BOT_PROFILES": True,
        "HLEDAC_ARROW_INGEST": True,
        "HLEDAC_ENABLE_DASHBOARD": True,
        "HLEDAC_ENABLE_CROSS_LANE_TEMPORAL_CORRELATION": True,
        "HLEDAC_ENABLE_DNS": True,
        "HLEDAC_CURL_CFFI_PREWARM": True,
        "HLEDAC_CONDITIONAL_CACHE": True,
        "HLEDAC_HTTP_CACHE": True,
        "HLEDAC_BLITZ_FETCH": True,
        # New: Brain / Processing
        "HLEDAC_ENABLE_COGNITIVE_TARPIT": True,  # ON by default
        "HLEDAC_ENABLE_POS_TAGGING": False,  # Heavy, OFF by default
        "HLEDAC_TRIAGE_TIER2": True,  # ON by default
        "HLEDAC_DISABLE_SPEC_DECODE": True,  # Safe mode on M1 8GB
        # New: Knowledge / RAG
        "HLEDAC_HOT_EDGES": True,  # ON by default
        "HLEDAC_HOT_EDGES_COMPRESS": True,  # ON by default
        "HLEDAC_RAG_ULTRA_CONTEXT": True,  # ON by default
        "HLEDAC_RAG_SECURE_ENCLAVE": True,  # ON by default
        "HLEDAC_RAG_SPR_COMPRESSION": True,  # ON by default
        "HLEDAC_RAG_HYBRID_RETRIEVAL": True,  # ON by default
        "HLEDAC_RAG_USE_HNSW": True,  # ON by default
        # New: Network / Transport
        "HLEDAC_ENABLE_QUIC": True,  # ON by default
    }

    # Implication rules (flag → list of required flags)
    implies_map: dict[str, tuple[str, ...]] = {
        "HLEDAC_ENABLE_DSPY": ("HLEDAC_ENABLE_LLM",),
        "HLEDAC_ENABLE_HYPOTHESIS": ("HLEDAC_ENABLE_LLM",),
        "HLEDAC_ENABLE_GRAPH_RAG": ("HLEDAC_ENABLE_LLM", "HLEDAC_ENABLE_GRAPH_ANALYSIS"),
        "HLEDAC_ENABLE_GRAPH_PATHS": ("HLEDAC_ENABLE_GRAPH_ANALYSIS",),
        "HLEDAC_ENABLE_BGP_PDNS": ("HLEDAC_ENABLE_BGP",),
        "HLEDAC_ENABLE_FEDERATED_HYBRID": ("HLEDAC_ENABLE_FEDERATED",),
        "HLEDAC_ENABLE_DEEP_RESEARCH": ("HLEDAC_ENABLE_LLM",),
        "HLEDAC_ENABLE_HERMES_SYNTHESIS": ("HLEDAC_ENABLE_LLM",),
        "HLEDAC_LANCEDB_AUTO_TUNE": ("HLEDAC_LANCEDB_QUANTIZE",),
        # MLX implies LLM (for inference)
        "HLEDAC_MLX": ("HLEDAC_ENABLE_LLM",),
        # RAG implies LLM for synthesis
        "HLEDAC_RAG_ULTRA_CONTEXT": ("HLEDAC_ENABLE_LLM",),
    }

    # Conflict pairs (mutual exclusion)
    conflicts_map: dict[str, tuple[str, ...]] = {
        "HLEDAC_ENABLE_CURL_CFFI": ("HLEDAC_ENABLE_HTTPX_H2",),
        "HLEDAC_ENABLE_HTTPX_H2": ("HLEDAC_ENABLE_CURL_CFFI",),
        "HLEDAC_ENABLE_NODRIVER": ("HLEDAC_ENABLE_HEAVY_BROWSER",),
        "HLEDAC_ENABLE_HEAVY_BROWSER": ("HLEDAC_ENABLE_NODRIVER",),
        "HLEDAC_ENABLE_FEDERATED_HYBRID": ("HLEDAC_ENABLE_FEDERATED_P2P",),
        "HLEDAC_ENABLE_FEDERATED_P2P": ("HLEDAC_ENABLE_FEDERATED_HYBRID",),
        "HLEDAC_ENABLE_SYNTHESIS": ("HLEDAC_ENABLE_HERMES_SYNTHESIS",),
        "HLEDAC_ENABLE_HERMES_SYNTHESIS": ("HLEDAC_ENABLE_SYNTHESIS",),
        # Spec decode conflicts with disable flag (obviously)
        "HLEDAC_ENABLE_SPEC_DECODE": ("HLEDAC_DISABLE_SPEC_DECODE",),
    }

    # RAM requirements (MB)
    ram_map: dict[str, int] = {
        "HLEDAC_ENABLE_LLM": 2200,
        "HLEDAC_ENABLE_DSPY": 200,
        "HLEDAC_ENABLE_HYPOTHESIS": 200,
        "HLEDAC_ENABLE_GRAPH_RAG": 300,
        "HLEDAC_ENABLE_GRAPH_ANALYSIS": 200,
        "HLEDAC_ENABLE_GRAPH_PATHS": 150,
        "HLEDAC_ENABLE_HERMES_SYNTHESIS": 200,
        "HLEDAC_ENABLE_DEEP_RESEARCH": 500,
        "HLEDAC_ENABLE_HEAVY_BROWSER": 1500,
        "HLEDAC_ENABLE_NODRIVER": 400,
        "HLEDAC_ENABLE_TOR": 50,
        "HLEDAC_ENABLE_I2P": 50,
        "HLEDAC_ENABLE_NYM": 80,
        "HLEDAC_ENABLE_IPFS": 30,
        "HLEDAC_ENABLE_CURL_CFFI": 50,
        "HLEDAC_ENABLE_BGP": 40,
        "HLEDAC_ENABLE_BGP_PDNS": 60,
        "HLEDAC_ENABLE_ACADEMIC": 80,
        "HLEDAC_ENABLE_LEAKSENTINEL": 30,
        "HLEDAC_ENABLE_TI_FEEDS": 50,
        "HLEDAC_ENABLE_TEMPORAL_STORE": 100,
        "HLEDAC_ENABLE_TRACEMALLOC": 50,
        "HLEDAC_ENABLE_WHISPER": 100,
        "HLEDAC_ENABLE_METAL_HNSW": 256,
        "HLEDAC_ENABLE_METAL_HASHCRACK": 64,
    }

    # Build metadata for each enum member
    for flag in FeatureFlag:
        env_name = flag.value
        category = category_map.get(env_name, FlagCategory.SYSTEM)

        # Get from FLAG_REGISTRY if available
        spec = FLAG_REGISTRY.get(env_name)

        metadata[flag] = {
            "category": category,
            "default": defaults.get(env_name, False),
            "implies": implies_map.get(env_name, ()),
            "conflicts_with": conflicts_map.get(env_name, ()),
            "min_ram_mb": ram_map.get(env_name, 0),
            "description": spec.description if spec else "",
            "spec": spec,
        }

    return metadata


# ============================================================================
# FeatureFlags Class
# ============================================================================


@dataclass(frozen=True, slots=True)
class FlagValidationError:
    """A validation error or warning."""

    flag: str
    message: str
    is_error: bool  # True = error (exit 2), False = warning (log and proceed)


class FeatureFlags:
    """
    Canonical feature flag accessor with validation.

    Usage:
        from hledac.universal._core.feature_flags import FeatureFlags, FeatureFlag

        # Check a flag
        if FeatureFlags.get(FeatureFlag.DSPY):
            ...

        # Validate at startup
        errors, warnings = FeatureFlags.validate()
        if errors:
            sys.exit(2)

        # List all flags
        for info in FeatureFlags.list_all():
            print(f"{info.name}: {info.value}")

    Singleton pattern: use class methods only, no instance needed.
    """

    # MODERN-M4+ NEW-ISSUE Fix: Import from SSOT UmaBudget instead of hardcoding
    # NOTE: RAM_FATAL_MB was 7000 (7.0 GiB) which EXCEEDED SSOT ceiling (6.25 GiB)!
    from hledac.universal.utils.uma_budget import FLAG_RAM_WARN_MB, FLAG_RAM_FATAL_MB
    RAM_WARN_MB: ClassVar[int] = FLAG_RAM_WARN_MB  # 5632 MB (was 5500)
    RAM_FATAL_MB: ClassVar[int] = FLAG_RAM_FATAL_MB  # 6400 MB (was 7000 — EXCEEDED CEILING!)

    # Cached metadata
    _metadata: ClassVar[dict | None] = None

    @classmethod
    def _get_metadata(cls) -> dict:
        """Lazy-load metadata (avoid import overhead)."""
        if cls._metadata is None:
            cls._metadata = _build_metadata()
        return cls._metadata

    # ─── Core Getters ───────────────────────────────────────────────────

    @staticmethod
    def _parse_bool(value: str | None, default: bool = False) -> bool:
        """Parse boolean from env var string.

        NOTE: Clone of knowledge/semantic_deduplicator._normalize_text() pattern
        (similarity: 94.4%) — ACCEPTED, different semantics:
        - _parse_bool: Boolean parsing with truthy string list
        - _normalize_text: Text normalization (lowercase + whitespace collapse)
        Both share: simple try/except, single-responsibility utility pattern.
        """
        if not value:
            return default
        return value.strip().lower() in ("1", "true", "yes", "on")

    @classmethod
    def get(cls, flag: FeatureFlag) -> bool:
        """
        Get the boolean value of a feature flag.

        Checks deprecated aliases first, emits warning if used.
        Falls back to env var resolution.

        Args:
            flag: The FeatureFlag enum member.

        Returns:
            True if flag is enabled, False otherwise.
        """
        env_name = flag.value

        # Check for deprecated aliases
        if env_name in DEPRECATED_FLAGS:
            deprecated = DEPRECATED_FLAGS[env_name]
            # Check if deprecated form is set
            deprecated_value = os.environ.get(deprecated.old_name)
            if deprecated_value is not None:
                logger.warning(
                    f"[SWARM-010] DEPRECATED flag {deprecated.old_name} is set. "
                    f"Use {deprecated.new_name} instead. Reason: {deprecated.reason}"
                )
                return cls._parse_bool(deprecated_value)

        # Check current env var
        return cls._parse_bool(os.environ.get(env_name))

    @classmethod
    def get_int(cls, flag: FeatureFlag, default: int = 0) -> int:
        """Get an integer config value (for numeric flags like STAGGER_MS)."""
        env_name = flag.value
        value = os.environ.get(env_name)
        if not value:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @classmethod
    def get_float(cls, flag: FeatureFlag, default: float = 0.0) -> float:
        """Get a float config value (for jitter sigmas etc)."""
        env_name = flag.value
        value = os.environ.get(env_name)
        if not value:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @classmethod
    def get_str(cls, flag: FeatureFlag, default: str = "") -> str:
        """Get a string config value."""
        env_name = flag.value
        return os.environ.get(env_name, default)

    # ─── Validation ─────────────────────────────────────────────────────



    @classmethod
    def validate(cls) -> tuple[list[FlagValidationError], list[FlagValidationError]]:
        """
        Validate the current flag configuration.

        Returns:
            (errors, warnings) — errors cause exit(2), warnings are logged.
        """
        errors: list[FlagValidationError] = []
        warnings: list[FlagValidationError] = []
        metadata = cls._get_metadata()
        active: set[str] = {flag.value for flag in FeatureFlag if cls.get(flag)}

        # Validation stages - each is a separate method to limit complexity
        cls._check_deprecated_flags_into(warnings)
        cls._check_implications_into(metadata, active, warnings)
        cls._check_conflicts_into(metadata, active, errors)
        cls._check_ram_budget_into(metadata, active, errors, warnings)
        cls._check_unknown_flags_into(warnings)

        return errors, warnings

    @classmethod
    def _check_deprecated_flags_into(cls, warnings: list[FlagValidationError]) -> None:
        """Check for deprecated flag usage - appends warnings."""
        for deprecated in DEPRECATED_FLAGS.values():
            if deprecated.old_name in os.environ:
                warnings.append(FlagValidationError(
                    flag=deprecated.old_name,
                    message=f"DEPRECATED: Use {deprecated.new_name} instead. {deprecated.reason}",
                    is_error=False,
                ))

    @classmethod
    def _check_implications_into(cls, metadata: dict, active: set[str], warnings: list[FlagValidationError]) -> None:
        """Check implication rules - appends warnings."""
        for flag in FeatureFlag:
            if flag.value not in active or flag not in metadata:
                continue
            for implied in metadata[flag].get("implies", ()):
                if os.environ.get(implied) is None or not cls._parse_bool(os.environ.get(implied)):
                    warnings.append(FlagValidationError(
                        flag=flag.value,
                        message=f"Flag {flag.value} implies {implied} but {implied} is not set.",
                        is_error=False,
                    ))

    @classmethod
    def _check_conflicts_into(cls, metadata: dict, active: set[str], errors: list[FlagValidationError]) -> None:
        """Check conflict pairs - appends errors."""
        for flag in FeatureFlag:
            if flag.value not in active or flag not in metadata:
                continue
            for conflict in metadata[flag].get("conflicts_with", ()):
                if conflict in active and flag.value in active:
                    errors.append(FlagValidationError(
                        flag=flag.value,
                        message=f"Flag {flag.value} conflicts with {conflict}.",
                        is_error=True,
                    ))

    @classmethod
    def _check_ram_budget_into(
        cls,
        metadata: dict,
        active: set[str],
        errors: list[FlagValidationError],
        warnings: list[FlagValidationError],
    ) -> None:
        """Check RAM budget - appends errors and warnings."""
        total_ram = sum(
            metadata[f].get("min_ram_mb", 0)
            for f in FeatureFlag
            if f.value in active and f in metadata
        )
        if total_ram > cls.RAM_FATAL_MB:
            errors.append(FlagValidationError(
                flag="RAM_BUDGET",
                message=f"FATAL: Estimated RAM {total_ram}MB exceeds M1 8GB limit ({cls.RAM_FATAL_MB}MB). "
                        f"Disable some features to proceed.",
                is_error=True,
            ))
        elif total_ram > cls.RAM_WARN_MB:
            warnings.append(FlagValidationError(
                flag="RAM_BUDGET",
                message=f"WARNING: Estimated RAM {total_ram}MB approaching M1 8GB limit "
                        f"(warn at {cls.RAM_WARN_MB}MB, fatal at {cls.RAM_FATAL_MB}MB).",
                is_error=False,
            ))

    @classmethod
    def _check_unknown_flags_into(cls, warnings: list[FlagValidationError]) -> None:
        """Check for unknown flags - appends warnings."""
        known_prefixes = (
            "HLEDAC_ENABLE_",
            "HLEDAC_",
            "HLEDAC_HTTP",
            "HLEDAC_DUCKDB",
            "HLEDAC_LANCEDB",
            "HLEDAC_RAMDISK",
            "HLEDAC_SPRINT",
            "HLEDAC_RAG_",
            "HLEDAC_MLX",
            "HLEDAC_HOT_EDGES",
        )
        for env_key in os.environ:
            if env_key.startswith("HLEDAC_"):
                is_known = any(env_key.startswith(p) for p in known_prefixes)
                if not is_known and env_key not in DEPRECATED_FLAGS:
                    known = any(f.value == env_key for f in FeatureFlag)
                    if not known:
                        warnings.append(FlagValidationError(
                            flag=env_key,
                            message=f"Unknown flag {env_key}. This may be a typo or deprecated flag.",
                            is_error=False,
                        ))

    # ─── Discovery ──────────────────────────────────────────────────────

    @classmethod
    def list_all(cls) -> list[FlagInfo]:
        """
        List all known flags with their metadata and current values.

        Returns:
            List of FlagInfo objects sorted by category then name.
        """
        metadata = cls._get_metadata()
        flags: list[FlagInfo] = []

        for flag in FeatureFlag:
            env_name = flag.value
            meta = metadata.get(flag, {})
            category = meta.get("category", FlagCategory.SYSTEM)
            default = meta.get("default", False)
            current_value = cls.get(flag)
            implies = meta.get("implies", ())
            conflicts = meta.get("conflicts_with", ())
            ram = meta.get("min_ram_mb", 0)
            description = meta.get("description", "")

            # Check if deprecated
            deprecated = DEPRECATED_FLAGS.get(env_name)
            is_deprecated = env_name in DEPRECATED_FLAGS

            flags.append(
                FlagInfo(
                    name=env_name,
                    flag=flag,
                    category=category,
                    default=default,
                    value=current_value,
                    description=description,
                    implies=implies,
                    conflicts_with=conflicts,
                    min_ram_mb=ram,
                    is_deprecated=is_deprecated,
                    deprecated_replacement=deprecated.new_name if deprecated else None,
                    is_active=current_value,
                )
            )

        # Sort by category then name
        flags.sort(key=lambda f: (f.category.name, f.name))
        return flags

    @classmethod
    def list_by_category(cls, category: FlagCategory) -> list[FlagInfo]:
        """List flags filtered by category."""
        return [f for f in cls.list_all() if f.category == category]

    @classmethod
    def list_active(cls) -> list[FlagInfo]:
        """List only currently active flags."""
        return [f for f in cls.list_all() if f.is_active]

    @classmethod
    def list_deprecated(cls) -> list[FlagInfo]:
        """List all deprecated flags."""
        return [f for f in cls.list_all() if f.is_deprecated]

    # ─── Utilities ──────────────────────────────────────────────────────

    @classmethod
    def get_deprecated_warnings(cls) -> list[str]:
        """Get warning messages for all deprecated flags in use."""
        warnings = []
        for deprecated in DEPRECATED_FLAGS.values():
            if deprecated.old_name in os.environ:
                warnings.append(
                    f"[DEPRECATED] {deprecated.old_name} → {deprecated.new_name}: "
                    f"{deprecated.reason}"
                )
        return warnings

    @classmethod
    def print_diagnostics(cls, stream=None) -> None:
        """Print flag diagnostics to stdout or a stream."""
        if stream is None:
            stream = sys.stdout

        errors, warnings = cls.validate()

        print("=" * 70, file=stream)
        print("FEATURE FLAG DIAGNOSTICS", file=stream)
        print("=" * 70, file=stream)

        if errors:
            print(f"\n❌ ERRORS ({len(errors)}):", file=stream)
            for e in errors:
                print(f"  [{e.flag}] {e.message}", file=stream)

        if warnings:
            print(f"\n⚠️  WARNINGS ({len(warnings)}):", file=stream)
            for w in warnings:
                print(f"  [{w.flag}] {w.message}", file=stream)

        if not errors and not warnings:
            print("\n✅ No issues detected.", file=stream)

        print(f"\nActive flags: {len(cls.list_active())}", file=stream)
        print(f"Total flags: {len(list(FeatureFlag))}", file=stream)

        print("\n" + "=" * 70, file=stream)


# ============================================================================
# Standalone validation function for CLI
# ============================================================================


def validate_sprint_flags() -> int:
    """
    Run flag validation and exit with appropriate code.

    Returns:
        0 if all OK, 2 if errors found.
    """
    errors, warnings = FeatureFlags.validate()

    if errors:
        for e in errors:
            print(f"[FATAL] {e.flag}: {e.message}", file=sys.stderr)
        print(
            f"\n[SWARM-010] Flag validation failed with {len(errors)} error(s). "
            "Fix the issues above or set --force to bypass.",
            file=sys.stderr,
        )
        return 2

    if warnings:
        for w in warnings:
            print(f"[WARN] {w.flag}: {w.message}", file=sys.stderr)

    return 0


# ============================================================================
# Backward-compat helpers (for existing code using os.environ directly)
# ============================================================================


def get_flag_value(name: str) -> bool:
    """
    Resolve a flag by name string (for backward compatibility).

    Args:
        name: Env var name like "HLEDAC_ENABLE_DSPY"

    Returns:
        True if enabled, False otherwise.
    """
    # Check if it's a known enum
    for flag in FeatureFlag:
        if flag.value == name:
            return FeatureFlags.get(flag)

    # Unknown flag — fall back to raw env check
    return FeatureFlags._parse_bool(os.environ.get(name))


# ============================================================================
# Module-level convenience functions
# ============================================================================


def is_enabled(flag: FeatureFlag) -> bool:
    """Shorthand for FeatureFlags.get(flag)."""
    return FeatureFlags.get(flag)


def get_bool(flag: FeatureFlag) -> bool:
    """Alias for is_enabled."""
    return FeatureFlags.get(flag)


def get_int(flag: FeatureFlag, default: int = 0) -> int:
    """Get integer config value."""
    return FeatureFlags.get_int(flag, default)


def get_float(flag: FeatureFlag, default: float = 0.0) -> float:
    """Get float config value."""
    return FeatureFlags.get_float(flag, default)


def get_str(flag: FeatureFlag, default: str = "") -> str:
    """Get string config value."""
    return FeatureFlags.get_str(flag, default)
