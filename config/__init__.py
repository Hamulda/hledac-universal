"""
hledac.universal.config — canonical config namespace for universal package
========================================================================






All content migrated from hledac/universal/config.py (single-file).
"""

import os
from dataclasses import field
from pathlib import Path
from typing import Any

from compat.msgspec_gc_compat import Struct
from hledac.universal.project_types import (
    AgentManagerConfig,
    CommunicationConfig,
    CoordinationConfig,
    DeepResearchConfig,
    GhostConfig,
    MemoryConfig,
    ResearchConfig,
    ResearchMode,
)

from .settings import (
    GLINER_MODEL_DEFAULT,
    HERMES_MODEL_DEFAULT,
    MODERNBERT_MODEL_DEFAULT,
    CooldownSettings,
    DedupSettings,
    DuckDBSettings,
    FeatureGates,
    FetchSettings,
    GraphSettings,
    MemorySettings,
    MLXSettings,
    Settings,
    SprintSettings,
    SynthesisSettings,
    TransportSettings,
    settings,
)


class M1Presets:
    """Research-mode presets for M1 hardware.

    NOTE: Hardware limits (memory, thermal, circuit_breaker_threshold) are
    centralized in M1AirConfig (core/config/m1_air_config.py).
    These presets are for research-mode behavior tuning only.

    ISSUE-015: Model constants now reference canonical values from settings.
    """

    MEMORY_LIMIT_MB = 5500.0
    THERMAL_THRESHOLD_C = 85.0
    # ISSUE-015: Use canonical constants
    HERMES_MODEL: str = HERMES_MODEL_DEFAULT
    MODERNBERT_MODEL: str = MODERNBERT_MODEL_DEFAULT
    GLINER_MODEL: str = GLINER_MODEL_DEFAULT
    MAX_CONCURRENT_AGENTS = 6
    AGENT_TIMEOUT_SECONDS = 25.0
    CONTEXT_SWAP_ENABLED = True
    MLX_CACHE_CLEAR_INTERVAL = 10


class ResearchPresets:
    QUICK = {
        "max_steps": 5,
        "max_time_minutes": 5,
        "max_concurrent_agents": 2,
        "enable_knowledge_graph": False,
        "enable_rag": False,
    }
    STANDARD = {
        "max_steps": 20,
        "max_time_minutes": 30,
        "max_concurrent_agents": 4,
        "enable_knowledge_graph": False,
        "enable_rag": True,
    }
    DEEP = {
        "max_steps": 50,
        "max_time_minutes": 120,
        "max_concurrent_agents": 6,
        "enable_knowledge_graph": True,
        "enable_rag": True,
    }
    EXTREME = {
        "max_steps": 100,
        "max_time_minutes": 480,
        "max_concurrent_agents": 6,
        "enable_knowledge_graph": True,
        "enable_rag": True,
        "enable_fact_checking": True,
        "save_intermediate": True,
    }
    AUTONOMOUS = {
        "max_steps": 200,
        "max_time_minutes": 1440,
        "max_concurrent_agents": 6,
        "enable_knowledge_graph": True,
        "enable_rag": True,
        "enable_fact_checking": True,
        "save_intermediate": True,
        "auto_archive_fallback": True,
    }

    @classmethod
    def get_preset(cls, mode: ResearchMode) -> dict[str, Any]:
        return {
            ResearchMode.QUICK: cls.QUICK,
            ResearchMode.STANDARD: cls.STANDARD,
            ResearchMode.DEEP: cls.DEEP,
            ResearchMode.EXTREME: cls.EXTREME,
            ResearchMode.AUTONOMOUS: cls.AUTONOMOUS,
        }.get(mode, cls.STANDARD)


class SecurityConfig(Struct):
    obfuscation_level: str = "medium"
    generate_decoys: bool = True
    decoy_count: int = 20
    wipe_standard: str = "nist_800_88"
    verification_enabled: bool = True
    rename_before_delete: bool = True
    enable_query_masking: bool = True
    enable_chaff_traffic: bool = True
    chaff_ratio: float = 0.3
    enable_timing_jitter: bool = True
    jitter_percent: float = 50.0
    privacy_level: str = "high"
    enable_audit_logging: bool = True
    anonymize_pii: bool = True


class StealthConfig(Struct, frozen=True):
    browser_type: str = "chromium"
    headless: bool = True
    pool_size: int = 2
    enable_stealth_scripts: bool = True
    enable_fingerprint_rotation: bool = True
    fingerprint_count: int = 50
    enable_canvas_noise: bool = True
    enable_webgl_spoofing: bool = True
    detection_threshold: float = 0.7
    adaptive_mode: bool = True
    enable_behavior_simulation: bool = True
    enable_captcha_solving: bool = True
    enable_captcha_local: bool = False
    captcha_providers: list[str] = field(default_factory=lambda: ["2captcha", "anticaptcha"])
    captcha_timeout: int = 120
    enable_proxy_rotation: bool = False
    proxy_list: list[str] = field(default_factory=list)


class PrivacyConfig(Struct, frozen=True):
    enable_vpn: bool = False
    vpn_config_path: str | None = None
    enable_tor: bool = False
    # OPSEC-001: socks5h:// forces remote DNS resolution by Tor proxy.
    tor_proxy: str = os.environ.get("TOR_PROXY_URL", "socks5h://127.0.0.1:9050")
    enable_dns_encryption: bool = True
    dns_servers: list[str] = field(default_factory=lambda: ["1.1.1.1", "9.9.9.9"])
    use_doh: bool = False
    enable_encryption: bool = True
    encryption_algorithm: str = "fernet"


class UniversalConfig(Struct, frozen=True):
    mode: ResearchMode = ResearchMode.STANDARD
    research: ResearchConfig = field(default_factory=ResearchConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    ghost: GhostConfig = field(default_factory=GhostConfig)
    coordination: CoordinationConfig = field(default_factory=CoordinationConfig)
    agent_manager: AgentManagerConfig = field(default_factory=AgentManagerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    stealth: StealthConfig = field(default_factory=StealthConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    deep_research: DeepResearchConfig = field(default_factory=DeepResearchConfig)
    communication: CommunicationConfig = field(default_factory=CommunicationConfig)
    db_path: str | None = None
    vault_path: str | None = None
    models_dir: str | None = None
    enable_ghost_layer: bool = True
    enable_coordination_layer: bool = True
    enable_knowledge_layer: bool = False
    enable_rag_pipeline: bool = False
    enable_reasoning_engine: bool = True
    enable_security_layer: bool = True
    enable_stealth_layer: bool = True
    enable_privacy_layer: bool = False
    enable_deep_research: bool = True
    enable_communication_layer: bool = True
    log_level: str = "INFO"
    log_to_file: bool = True
    log_dir: str = "logs"
    m1_optimized: bool = True
    context_swap_enabled: bool = True
    enable_thermal_management: bool = True
    enable_moe_router: bool = True
    enable_moe_synthesis: bool = True
    moe_max_active_experts: int = 2
    enable_neuromorphic: bool = True
    snn_n_neurons: int = 500
    snn_connection_prob: float = 0.05
    snn_enable_stdp: bool = True
    enable_federated_osint: bool = False
    federated_dp_epsilon: float = 0.1
    federated_batch_size: int = 16
    federated_round_interval_hours: int = 24
    enable_quantum_pathfinding: bool = True
    quantum_max_steps: int = 50
    quantum_amplification_strength: float = 1.5
    quantum_max_nodes: int = 5000
    enable_distillation: bool = False
    distillation_hidden_dim: int = 128
    distillation_learning_rate: float = 0.001
    enable_agent_meta_optimization: bool = True
    agent_meta_optimization_interval: int = 10
    agent_meta_min_samples: int = 5
    tot_enabled: bool = True
    tot_complexity_threshold: float = 0.7
    tot_hybrid_threshold: float = 0.45
    tot_max_depth: int = 5
    tot_max_time: float = 120.0
    tot_enable_backtracking: bool = True
    tot_enable_mcts: bool = True
    enable_steganography_detection: bool = True
    stego_chi_square_threshold: float = 0.05
    stego_rs_analysis_enabled: bool = True
    stego_dct_analysis_enabled: bool = True
    stego_max_image_size: int = 2048
    enable_dns_tunnel_detection: bool = True
    dns_entropy_threshold: float = 4.2
    dns_ngram_threshold: float = 0.7
    dns_lstm_threshold: float = 0.8
    dns_max_queries_per_batch: int = 1000
    dns_enable_lstm: bool = True
    dns_pcap_chunk_seconds: int = 60
    enable_unicode_attack_detection: bool = True
    unicode_detect_zero_width: bool = True
    unicode_detect_homoglyphs: bool = True
    unicode_detect_bidi_attacks: bool = True
    unicode_detect_normalization: bool = True
    unicode_chunk_size: int = 1048576
    enable_metadata_extraction: bool = True
    metadata_extract_exif: bool = True
    metadata_extract_gps: bool = True
    metadata_reverse_geocode: bool = False
    metadata_extract_audio: bool = True
    metadata_extract_video: bool = False
    metadata_calculate_hashes: bool = True
    metadata_hash_algorithms: list[str] = field(default_factory=lambda: ["md5", "sha256"])
    metadata_max_file_size: int = 1073741824
    metadata_batch_size: int = 100
    enable_encoding_detection: bool = True
    encoding_min_length: int = 20
    encoding_detect_nested: bool = True
    encoding_max_depth: int = 5
    encoding_chunk_size: int = 1048576
    enable_hash_identification: bool = True
    hash_min_confidence: float = 0.3
    hash_top_k_results: int = 3
    hash_detect_salted: bool = True
    hash_batch_size: int = 1000
    enable_autonomous_intelligence: bool = True
    intelligence_decision_threshold: float = 0.3
    intelligence_max_parallel_modules: int = 4
    intelligence_module_timeout: int = 60
    intelligence_enable_learning: bool = True
    intelligence_cache_results: bool = True
    intelligence_cache_ttl: int = 3600
    analysis_mode_default: str = "auto"
    quick_scan_time_limit: int = 5
    deep_analysis_modules: list[str] = field(default_factory=list)

    @classmethod
    def for_mode(cls, mode: ResearchMode, m1_optimized: bool = True) -> UniversalConfig:
        preset = ResearchPresets.get_preset(mode)
        config = cls(
            mode=mode,
            enable_knowledge_layer=mode in [ResearchMode.DEEP, ResearchMode.EXTREME, ResearchMode.AUTONOMOUS],
            enable_rag_pipeline=mode
            in [ResearchMode.STANDARD, ResearchMode.DEEP, ResearchMode.EXTREME, ResearchMode.AUTONOMOUS],
            m1_optimized=m1_optimized,
        )
        config.research.mode = mode
        config.research.max_steps = preset.get("max_steps", 20)
        config.research.max_time_minutes = preset.get("max_time_minutes", 30)
        config.research.max_concurrent_agents = preset.get("max_concurrent_agents", 3)
        config.research.enable_knowledge_graph = preset.get("enable_knowledge_graph", False)
        config.research.enable_rag = preset.get("enable_rag", True)
        config.research.enable_fact_checking = preset.get("enable_fact_checking", False)
        config.research.save_intermediate = preset.get("save_intermediate", False)
        if m1_optimized:
            config._apply_m1_optimizations()
        return config

    def _apply_m1_optimizations(self) -> None:
        # ISSUE-7.1: circuit_breaker_threshold from M1AirConfig (=5, not M1Presets=3)
        from hledac.universal._core.config.m1_air_config import M1AirConfig

        self.memory.memory_limit_mb = M1Presets.MEMORY_LIMIT_MB
        self.memory.thermal_threshold_c = M1Presets.THERMAL_THRESHOLD_C
        self.research.hermes_model = M1Presets.HERMES_MODEL
        self.research.modernbert_model = M1Presets.MODERNBERT_MODEL
        self.research.gliner_model = M1Presets.GLINER_MODEL
        self.agent_manager.max_concurrent_agents = min(
            self.agent_manager.max_concurrent_agents, M1Presets.MAX_CONCURRENT_AGENTS
        )
        self.agent_manager.agent_timeout_seconds = M1Presets.AGENT_TIMEOUT_SECONDS
        self.agent_manager.circuit_breaker_threshold = M1AirConfig.circuit_breaker_threshold  # 5 (tightened for M1)
        if self.research.max_concurrent_agents > 4:
            self.enable_knowledge_layer = False
        self.coordination.max_context_length = 1024
        self.coordination.temperature = 0.1
        self.quantum_max_nodes = min(self.quantum_max_nodes, 5000)
        self.distillation_hidden_dim = min(self.distillation_hidden_dim, 128)

    @classmethod
    def from_env(cls) -> UniversalConfig:
        mode_str = os.getenv("HLEDAC_RESEARCH_MODE", "standard").upper()
        try:
            mode = ResearchMode[mode_str]
        except KeyError:
            mode = ResearchMode.STANDARD
        m1_optimized = os.getenv("HLEDAC_M1_OPTIMIZED", "true").lower() == "true"
        config = cls.for_mode(mode, m1_optimized)
        if v := os.getenv("HLEDAC_MEMORY_LIMIT_MB"):
            config.memory.memory_limit_mb = float(v)
        if v := os.getenv("HLEDAC_MAX_STEPS"):
            config.research.max_steps = int(v)
        if v := os.getenv("HLEDAC_LOG_LEVEL"):
            config.log_level = v
        return config

    def update(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
            elif hasattr(self.research, key):
                setattr(self.research, key, value)
            elif hasattr(self.memory, key):
                setattr(self.memory, key, value)
            elif hasattr(self.ghost, key):
                setattr(self.ghost, key, value)
            elif hasattr(self.coordination, key):
                setattr(self.coordination, key, value)
            elif hasattr(self.agent_manager, key):
                setattr(self.agent_manager, key, value)
            elif hasattr(self.security, key):
                setattr(self.security, key, value)
            elif hasattr(self.stealth, key):
                setattr(self.stealth, key, value)
            elif hasattr(self.privacy, key):
                setattr(self.privacy, key, value)
            elif hasattr(self.deep_research, key):
                setattr(self.deep_research, key, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "research": self.research.__dict__,
            "memory": self.memory.__dict__,
            "ghost": self.ghost.__dict__,
            "coordination": self.coordination.__dict__,
            "agent_manager": self.agent_manager.__dict__,
            "security": self.security.__dict__,
            "stealth": self.stealth.__dict__,
            "privacy": self.privacy.__dict__,
            "deep_research": self.deep_research.__dict__,
            "enable_ghost_layer": self.enable_ghost_layer,
            "enable_coordination_layer": self.enable_coordination_layer,
            "enable_knowledge_layer": self.enable_knowledge_layer,
            "enable_rag_pipeline": self.enable_rag_pipeline,
            "enable_security_layer": self.enable_security_layer,
            "enable_stealth_layer": self.enable_stealth_layer,
            "enable_privacy_layer": self.enable_privacy_layer,
            "enable_deep_research": self.enable_deep_research,
            "enable_communication_layer": self.enable_communication_layer,
            "enable_quantum_pathfinding": self.enable_quantum_pathfinding,
            "quantum_max_steps": self.quantum_max_steps,
            "quantum_amplification_strength": self.quantum_amplification_strength,
            "quantum_max_nodes": self.quantum_max_nodes,
            "enable_distillation": self.enable_distillation,
            "distillation_hidden_dim": self.distillation_hidden_dim,
            "distillation_learning_rate": self.distillation_learning_rate,
            "enable_agent_meta_optimization": self.enable_agent_meta_optimization,
            "agent_meta_optimization_interval": self.agent_meta_optimization_interval,
            "agent_meta_min_samples": self.agent_meta_min_samples,
            "m1_optimized": self.m1_optimized,
        }

    def validate(self) -> list[str]:
        issues = []
        if self.memory.memory_limit_mb > 6000:
            issues.append("Memory limit exceeds safe M1 8GB threshold (6000MB)")
        if self.memory.memory_limit_mb < 2000:
            issues.append("Memory limit too low for meaningful operation")
        if self.research.max_steps < 1:
            issues.append("max_steps must be at least 1")
        if self.research.max_time_minutes < 1:
            issues.append("max_time_minutes must be at least 1")
        if self.agent_manager.max_concurrent_agents > 10:
            issues.append("max_concurrent_agents > 10 may cause memory issues")
        if self.m1_optimized:
            if self.enable_knowledge_layer and self.agent_manager.max_concurrent_agents > 4:
                issues.append("Warning: Knowledge layer with many agents may exceed M1 RAM")
            if self.enable_rag_pipeline and self.enable_knowledge_layer:
                issues.append("Warning: RAG + Knowledge layer may exceed M1 RAM")
        return issues


def create_config(
    mode: ResearchMode = ResearchMode.STANDARD, m1_optimized: bool = True, **overrides: Any
) -> UniversalConfig:
    config = UniversalConfig.for_mode(mode, m1_optimized)
    config.update(**overrides)
    return config


def load_config_from_file(path: str | Path) -> UniversalConfig:
    import json

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path) as f:
        data = json.load(f) if path.suffix == ".json" else {}
    mode = ResearchMode(data.get("mode", "standard"))
    m1_optimized = data.get("m1_optimized", True)
    config = UniversalConfig.for_mode(mode, m1_optimized)
    config.update(**{k: v for k, v in data.items() if k not in ["mode", "m1_optimized"]})
    return config


__all__ = [
    "settings",
    "Settings",
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
    "FeatureGates",
    "UniversalConfig",
    "create_config",
    "load_config_from_file",
    "M1Presets",
    "ResearchPresets",
    "SecurityConfig",
    "StealthConfig",
    "PrivacyConfig",
    "DeepResearchConfig",
    "ResearchMode",
    "ResearchConfig",
    "MemoryConfig",
    "GhostConfig",
    "CoordinationConfig",
    "AgentManagerConfig",
    "CommunicationConfig",
]
import threading
from typing import Final

from _core.lock_registry import LockCategory, register_lock

_adaptive_patches: dict[tuple[str, str], int | float | str] = {}


@register_lock(LockCategory.CONFIG)
def _adaptive_patches_lock() -> threading.Lock:
    """Module-level lock for adaptive config patches dict."""
    return threading.Lock()


class AdaptiveConfig:
    """
    Singleton adaptive configuration with env-var overrides + runtime patches.

    F290: Canonical replacement for hardcoded limits in transport/circuit_breaker.py
    and core/resource_governor.py. Provides:
    - Env-var overrides (HLEDAC_<SECTION>_<KEY>) with bounded validation
    - Runtime patch() for dynamic M1 8GB adaptation
    - Fail-safe: invalid env values → default, no exception

    Python 3.14 compatible — uses only stdlib.
    Thread-safe via threading.Lock for _patches updates.
    """

    _instance: AdaptiveConfig | None = None


@register_lock(LockCategory.CONFIG)
def _adaptive_config_lock() -> threading.Lock:
    """Module-level lock for AdaptiveConfig singleton factory."""
    return threading.Lock()

    def __init__(self) -> None:
        pass

    @classmethod
    def get(cls) -> AdaptiveConfig:
        if cls._instance is None:
            with _adaptive_config_lock():
                if cls._instance is None:
                    cls._instance = cls.__new__(cls)
        return cls._instance

    def get_int(self, section: str, key: str, default: int, min_val: int, max_val: int) -> int:
        patch_key = (section, key)
        with _adaptive_patches_lock():
            if patch_key in _adaptive_patches:
                raw = _adaptive_patches[patch_key]
                return self._clamp_int(raw, min_val, max_val, default)
        env_key = f"HLEDAC_{section}_{key}"
        raw = os.environ.get(env_key)
        if raw is not None:
            return self._clamp_int(raw, min_val, max_val, default)
        return default

    def get_float(self, section: str, key: str, default: float, min_val: float, max_val: float) -> float:
        patch_key = (section, key)
        with _adaptive_patches_lock():
            if patch_key in _adaptive_patches:
                raw = _adaptive_patches[patch_key]
                return self._clamp_float(raw, min_val, max_val, default)
        env_key = f"HLEDAC_{section}_{key}"
        raw = os.environ.get(env_key)
        if raw is not None:
            return self._clamp_float(raw, min_val, max_val, default)
        return default

    def patch(self, section: str, key: str, value: int | float | str) -> None:
        patch_key = (section, key)
        with _adaptive_patches_lock():
            _adaptive_patches[patch_key] = value

    def _clamp_int(self, raw: int | float | str, min_val: int, max_val: int, default: int) -> int:
        try:
            return max(min_val, min(max_val, int(raw)))
        except ValueError, TypeError:
            return default

    def _clamp_float(self, raw: int | float | str, min_val: float, max_val: float, default: float) -> float:
        try:
            return max(min_val, min(max_val, float(raw)))
        except ValueError, TypeError:
            return default


CB_CONFIG_DEFAULTS: Final[dict[str, tuple[str, int | float, int | float, int | float]]] = {
    "MAX_TRACKED_DOMAINS": ("CB_MAX_TRACKED_DOMAINS", 500, 50, 2000),
    "MAX_RECOVERY_TIMEOUT_S": ("CB_MAX_RECOVERY_TIMEOUT_S", 120.0, 15.0, 300.0),
    "BOOT_RECOVERY_TIMEOUT_S": ("CB_BOOT_RECOVERY_TIMEOUT_S", 5.0, 1.0, 30.0),
    "BASE_RECOVERY_TIMEOUT_S": ("CB_BASE_RECOVERY_TIMEOUT_S", 15.0, 5.0, 120.0),
    "BOOT_PHASE_DURATION_S": ("CB_BOOT_PHASE_DURATION_S", 60.0, 10.0, 300.0),
    "CIRCUIT_FAILURE_THRESHOLD": ("CB_CIRCUIT_FAILURE_THRESHOLD", 5, 1, 10),
    "CIRCUIT_HALF_OPEN_PROBES": ("CB_CIRCUIT_HALF_OPEN_PROBES", 3, 1, 5),
    "TIMEOUT_ACCUMULATOR_WEIGHT": ("CB_TIMEOUT_ACCUMULATOR_WEIGHT", 0.5, 0.1, 1.0),
    "CONSECUTIVE_TIMEOUT_ACCUMULATOR_THRESHOLD": ("CB_CONSECUTIVE_TIMEOUT_THRESHOLD", 4, 1, 10),
    "JITTER_MIN_MULTIPLIER": ("CB_JITTER_MIN_MULT", 0.5, 0.1, 1.0),
    "JITTER_MAX_MULTIPLIER": ("CB_JITTER_MAX_MULT", 1.5, 1.0, 3.0),
    "JITTER_MIN_FRACTION": ("CB_JITTER_MIN_FRACTION", 0.1, 0.01, 0.5),
}


def _cb_int(key: str) -> int:
    entry = CB_CONFIG_DEFAULTS[key]
    env_key = f"HLEDAC_{entry[0]}"
    raw = os.environ.get(env_key)
    if raw is not None:
        clamped = AdaptiveConfig.get()._clamp_int(raw, int(entry[2]), int(entry[3]), int(entry[1]))
        return clamped
    return int(entry[1])


def _cb_float(key: str) -> float:
    entry = CB_CONFIG_DEFAULTS[key]
    env_key = f"HLEDAC_{entry[0]}"
    raw = os.environ.get(env_key)
    if raw is not None:
        clamped = AdaptiveConfig.get()._clamp_float(raw, float(entry[2]), float(entry[3]), float(entry[1]))
        return clamped
    return float(entry[1])


# MODERN-36 Fix: SSOT values from UmaBudget (6.25 GiB ceiling on M1 8GB)
# Old values were based on 8GB * ratios (6.8, 7.0, 7.5, 7.8) which didn't align with SSOT.
# Now derives from UmaBudget.THRESHOLD_*_GIB values:
#   - THRESHOLD_SOFT_WARN_GIB = 5.5 (88% of 6.25)
#   - THRESHOLD_WARN_GIB = 5.938 (95% of 6.25)
#   - THRESHOLD_CRITICAL_GIB = 6.191 (99% of 6.25)
#   - THRESHOLD_EMERGENCY_GIB = 6.25 (100% = ceiling)
try:
    from hledac.universal.utils.uma_budget import SWAP_TIERS, UmaBudget

    # MODERN-41 Fix: Use SSOT SWAP_TIERS values for swap thresholds
    _RG_DEFAULTS = {
        "THRESHOLD_SOFT_WARN_GIB": (5.5, 5.0, 6.0),  # soft_warn (88% of ceiling)
        "THRESHOLD_WARN_GIB": (5.938, 5.5, 6.5),  # warn (95% of ceiling)
        "THRESHOLD_CRITICAL_GIB": (6.191, 5.8, 6.5),  # critical (99% of ceiling)
        "THRESHOLD_EMERGENCY_GIB": (6.25, 6.0, 7.0),  # emergency (ceiling)
        "HYSTERESIS_EXIT_GIB": (4.5, 4.0, 5.5),  # exit threshold
        # MODERN-41 Fix: SSOT SWAP_TIERS values
        "CLEAN_SWAP_MAX_GIB": (SWAP_TIERS.CLEAN, 1.0, 5.0),
        "DIAGNOSTIC_SWAP_MAX_GIB": (SWAP_TIERS.DIAGNOSTIC, 3.0, 6.0),
        "HARD_BLOCK_SWAP_GIB": (SWAP_TIERS.HARD_BLOCK, 4.0, 6.5),
        "HYSTERESIS_COOLDOWN_SEC": (2.0, 0.5, 10.0),
        "ALPHA_FAST": (0.4, 0.05, 0.9),
        "ALPHA_SLOW": (0.15, 0.01, 0.5),
        "MPC_HORIZON_S": (10.0, 5.0, 30.0),
        "TARGET_HEADROOM_GIB": (0.5, 0.1, 2.0),
        "EMERGENCY_THRESHOLD_GIB": (6.25, 6.0, 7.0),
    }
    RG_CONFIG_DEFAULTS: Final[dict[str, tuple[str, float, float, float]]] = {
        k: (f"RG_{k}", v[0], v[1], v[2]) for k, v in _RG_DEFAULTS.items()
    }
except ImportError:
    # Fallback: M1 8GB SSOT values (hardcoded to avoid import issues)
    # MODERN-41 Fix: Use SSOT SWAP_TIERS fallback values
    _FALLBACK_SWAP_TIERS = {"CLEAN": 3.3, "DIAGNOSTIC": 4.675, "HARD_BLOCK": 5.225}
    RG_CONFIG_DEFAULTS: Final[dict[str, tuple[str, float, float, float]]] = {
        "THRESHOLD_SOFT_WARN_GIB": ("RG_THRESHOLD_SOFT_WARN_GIB", 5.5, 5.0, 6.0),
        "THRESHOLD_WARN_GIB": ("RG_THRESHOLD_WARN_GIB", 5.938, 5.5, 6.5),
        "THRESHOLD_CRITICAL_GIB": ("RG_THRESHOLD_CRITICAL_GIB", 6.191, 5.8, 6.5),
        "THRESHOLD_EMERGENCY_GIB": ("RG_THRESHOLD_EMERGENCY_GIB", 6.25, 6.0, 7.0),
        "HYSTERESIS_EXIT_GIB": ("RG_HYSTERESIS_EXIT_GIB", 4.5, 4.0, 5.5),
        # MODERN-41 Fix: SSOT SWAP_TIERS fallback values
        "CLEAN_SWAP_MAX_GIB": ("RG_CLEAN_SWAP_MAX_GIB", _FALLBACK_SWAP_TIERS["CLEAN"], 1.0, 5.0),
        "DIAGNOSTIC_SWAP_MAX_GIB": ("RG_DIAGNOSTIC_SWAP_MAX_GIB", _FALLBACK_SWAP_TIERS["DIAGNOSTIC"], 3.0, 6.0),
        "HARD_BLOCK_SWAP_GIB": ("RG_HARD_BLOCK_SWAP_GIB", _FALLBACK_SWAP_TIERS["HARD_BLOCK"], 4.0, 6.5),
        "HYSTERESIS_COOLDOWN_SEC": ("RG_HYSTERESIS_COOLDOWN_SEC", 2.0, 0.5, 10.0),
        "ALPHA_FAST": ("RG_ALPHA_FAST", 0.4, 0.05, 0.9),
        "ALPHA_SLOW": ("RG_ALPHA_SLOW", 0.15, 0.01, 0.5),
        "MPC_HORIZON_S": ("RG_MPC_HORIZON_S", 10.0, 5.0, 30.0),
        "TARGET_HEADROOM_GIB": ("RG_TARGET_HEADROOM_GIB", 0.5, 0.1, 2.0),
        "EMERGENCY_THRESHOLD_GIB": ("RG_EMERGENCY_THRESHOLD_GIB", 6.25, 6.0, 7.0),
    }


def _rg_float(key: str) -> float:
    entry = RG_CONFIG_DEFAULTS[key]
    env_key = f"HLEDAC_{entry[0]}"
    raw = os.environ.get(env_key)
    if raw is not None:
        clamped = AdaptiveConfig.get()._clamp_float(raw, entry[2], entry[3], entry[1])
        return clamped
    return float(entry[1])
