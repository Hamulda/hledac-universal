"""
hledac.universal.config — canonical config namespace for universal package
========================================================================

All content migrated from hledac/universal/config.py (single-file).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hledac.universal.project_types import (
    AgentManagerConfig,
    CommunicationConfig,
    CoordinationConfig,
    GhostConfig,
    MemoryConfig,
    ResearchConfig,
    ResearchMode,
)


class M1Presets:
    MEMORY_LIMIT_MB = 5500.0
    THERMAL_THRESHOLD_C = 85.0
    HERMES_MODEL = "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"
    MODERNBERT_MODEL = "mlx-community/answerdotai-ModernBERT-base-6bit"
    GLINER_MODEL = "knowledgator/gliner-relex-large-v0.5"
    MAX_CONCURRENT_AGENTS = 6
    AGENT_TIMEOUT_SECONDS = 25.0
    CIRCUIT_BREAKER_THRESHOLD = 3
    CONTEXT_SWAP_ENABLED = True
    MLX_CACHE_CLEAR_INTERVAL = 10


class ResearchPresets:
    QUICK = {"max_steps": 5, "max_time_minutes": 5, "max_concurrent_agents": 2, "enable_knowledge_graph": False, "enable_rag": False}
    STANDARD = {"max_steps": 20, "max_time_minutes": 30, "max_concurrent_agents": 4, "enable_knowledge_graph": False, "enable_rag": True}
    DEEP = {"max_steps": 50, "max_time_minutes": 120, "max_concurrent_agents": 6, "enable_knowledge_graph": True, "enable_rag": True}
    EXTREME = {"max_steps": 100, "max_time_minutes": 480, "max_concurrent_agents": 6, "enable_knowledge_graph": True, "enable_rag": True, "enable_fact_checking": True, "save_intermediate": True}
    AUTONOMOUS = {"max_steps": 200, "max_time_minutes": 1440, "max_concurrent_agents": 6, "enable_knowledge_graph": True, "enable_rag": True, "enable_fact_checking": True, "save_intermediate": True, "auto_archive_fallback": True}

    @classmethod
    def get_preset(cls, mode):
        return {ResearchMode.QUICK: cls.QUICK, ResearchMode.STANDARD: cls.STANDARD, ResearchMode.DEEP: cls.DEEP, ResearchMode.EXTREME: cls.EXTREME, ResearchMode.AUTONOMOUS: cls.AUTONOMOUS}.get(mode, cls.STANDARD)


@dataclass
class SecurityConfig:
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


@dataclass
class StealthConfig:
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
    captcha_providers: list = field(default_factory=lambda: ["2captcha", "anticaptcha"])
    captcha_timeout: int = 120
    enable_proxy_rotation: bool = False
    proxy_list: list = field(default_factory=list)


@dataclass
class PrivacyConfig:
    enable_vpn: bool = False
    vpn_config_path = None
    enable_tor: bool = False
    tor_proxy: str = os.environ.get("TOR_PROXY_URL", "socks5://127.0.0.1:9050")
    enable_dns_encryption: bool = True
    dns_servers: list = field(default_factory=lambda: ["1.1.1.1", "9.9.9.9"])
    use_doh: bool = False
    enable_encryption: bool = True
    encryption_algorithm: str = "fernet"


@dataclass
class DeepResearchConfig:
    max_depth: int = 10
    strategy: str = "hybrid"
    follow_citations: bool = True
    explore_tangents: bool = True
    max_threads: int = 5
    max_documents: int = 1000
    max_citations_per_doc: int = 20
    citation_types: list = field(default_factory=lambda: ["academic", "patent", "preprint", "dataset"])
    enable_auto_summarize: bool = True
    summarization_model: str = "qwen3-1.7b"


@dataclass
class UniversalConfig:
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
    db_path = None
    vault_path = None
    models_dir = None
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
    tot_complexity_threshold: float = 0.70
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
    metadata_hash_algorithms: list = field(default_factory=lambda: ["md5", "sha256"])
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
    deep_analysis_modules: list = field(default_factory=list)

    @classmethod
    def for_mode(cls, mode, m1_optimized=True):
        preset = ResearchPresets.get_preset(mode)
        config = cls(
            mode=mode,
            enable_knowledge_layer=mode in [ResearchMode.DEEP, ResearchMode.EXTREME, ResearchMode.AUTONOMOUS],
            enable_rag_pipeline=mode in [ResearchMode.STANDARD, ResearchMode.DEEP, ResearchMode.EXTREME, ResearchMode.AUTONOMOUS],
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

    def _apply_m1_optimizations(self):
        self.memory.memory_limit_mb = M1Presets.MEMORY_LIMIT_MB
        self.memory.thermal_threshold_c = M1Presets.THERMAL_THRESHOLD_C
        self.research.hermes_model = M1Presets.HERMES_MODEL
        self.research.modernbert_model = M1Presets.MODERNBERT_MODEL
        self.research.gliner_model = M1Presets.GLINER_MODEL
        self.agent_manager.max_concurrent_agents = min(self.agent_manager.max_concurrent_agents, M1Presets.MAX_CONCURRENT_AGENTS)
        self.agent_manager.agent_timeout_seconds = M1Presets.AGENT_TIMEOUT_SECONDS
        self.agent_manager.circuit_breaker_threshold = M1Presets.CIRCUIT_BREAKER_THRESHOLD
        if self.research.max_concurrent_agents > 4:
            self.enable_knowledge_layer = False
        self.coordination.max_context_length = 1024
        self.coordination.temperature = 0.1
        self.quantum_max_nodes = min(self.quantum_max_nodes, 5000)
        self.distillation_hidden_dim = min(self.distillation_hidden_dim, 128)

    @classmethod
    def from_env(cls):
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

    def update(self, **kwargs):
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

    def to_dict(self):
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

    def validate(self):
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


def create_config(mode=ResearchMode.STANDARD, m1_optimized=True, **overrides):
    config = UniversalConfig.for_mode(mode, m1_optimized)
    config.update(**overrides)
    return config


def load_config_from_file(path):
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
    "UniversalConfig", "create_config", "load_config_from_file",
    "M1Presets", "ResearchPresets",
    "SecurityConfig", "StealthConfig", "PrivacyConfig", "DeepResearchConfig",
    "ResearchMode", "ResearchConfig", "MemoryConfig", "GhostConfig",
    "CoordinationConfig", "AgentManagerConfig", "CommunicationConfig",
]
