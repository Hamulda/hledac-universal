"""
Universal Orchestrator Configuration
====================================

Centralized configuration management for the universal orchestrator.
Supports:
- Environment-based configuration
- M1 8GB optimization presets
- Layer-specific settings
- Runtime configuration updates
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .project_types import (
    AgentManagerConfig,
    CommunicationConfig,
    CoordinationConfig,
    GhostConfig,
    MemoryConfig,
    ModelConfig,
    ResearchConfig,
    ResearchMode,
)


# =============================================================================
# DEFAULT CONFIGURATION PRESETS
# =============================================================================

class M1Presets:
    """M1 8GB RAM optimization presets"""
    
    # Strict memory limits
    MEMORY_LIMIT_MB = 5500.0
    THERMAL_THRESHOLD_C = 85.0
    
    # Model settings - 3 model stack only (M1 8GB optimized)
    HERMES_MODEL = "mlx-community/Hermes-3-Llama-3.2-3B-4bit"
    MODERNBERT_MODEL = "mlx-community/answerdotai-ModernBERT-base-6bit"
    GLINER_MODEL = "knowledgator/gliner-x-base"
    
    # Performance settings
    MAX_CONCURRENT_AGENTS = 6
    AGENT_TIMEOUT_SECONDS = 25.0
    CIRCUIT_BREAKER_THRESHOLD = 3
    
    # Memory management
    CONTEXT_SWAP_ENABLED = True
    MLX_CACHE_CLEAR_INTERVAL = 10  # Clear MLX cache every N transitions


class ResearchPresets:
    """Research mode presets"""
    
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
        "max_time_minutes": 1440,  # 24 hours
        "max_concurrent_agents": 6,
        "enable_knowledge_graph": True,
        "enable_rag": True,
        "enable_fact_checking": True,
        "save_intermediate": True,
        "auto_archive_fallback": True,
    }
    
    @classmethod
    def get_preset(cls, mode: ResearchMode) -> Dict[str, Any]:
        """Get preset configuration for research mode"""
        presets = {
            ResearchMode.QUICK: cls.QUICK,
            ResearchMode.STANDARD: cls.STANDARD,
            ResearchMode.DEEP: cls.DEEP,
            ResearchMode.EXTREME: cls.EXTREME,
            ResearchMode.AUTONOMOUS: cls.AUTONOMOUS,
        }
        return presets.get(mode, cls.STANDARD)


# =============================================================================
# EXTENDED CONFIGURATION CLASSES (NEW)
# =============================================================================

@dataclass
class SecurityConfig:
    """Security and cryptography configuration"""
    # Obfuscation
    obfuscation_level: str = "medium"  # none, light, medium, heavy, maximum
    generate_decoys: bool = True
    decoy_count: int = 20
    
    # Secure destruction
    wipe_standard: str = "nist_800_88"  # nist_800_88, dod_5220_22m, gutmann
    verification_enabled: bool = True
    rename_before_delete: bool = True
    
    # Research obfuscation
    enable_query_masking: bool = True
    enable_chaff_traffic: bool = True
    chaff_ratio: float = 0.3
    enable_timing_jitter: bool = True
    jitter_percent: float = 50.0
    
    # Privacy
    privacy_level: str = "high"  # low, medium, high, maximum
    enable_audit_logging: bool = True
    anonymize_pii: bool = True


@dataclass
class StealthConfig:
    """Stealth browsing and evasion configuration"""
    # Browser
    browser_type: str = "chromium"  # chromium, firefox, webkit
    headless: bool = True
    pool_size: int = 2
    
    # Anti-detection
    enable_stealth_scripts: bool = True
    enable_fingerprint_rotation: bool = True
    fingerprint_count: int = 50
    enable_canvas_noise: bool = True
    enable_webgl_spoofing: bool = True
    
    # Detection evasion
    detection_threshold: float = 0.7
    adaptive_mode: bool = True
    enable_behavior_simulation: bool = True
    
    # CAPTCHA
    enable_captcha_solving: bool = True
    captcha_providers: List[str] = field(default_factory=lambda: ["2captcha", "anticaptcha"])
    captcha_timeout: int = 120
    
    # Proxy
    enable_proxy_rotation: bool = False
    proxy_list: List[str] = field(default_factory=list)


@dataclass
class PrivacyConfig:
    """Privacy and anonymity configuration"""
    # VPN
    enable_vpn: bool = False
    vpn_config_path: Optional[str] = None
    
    # Tor
    enable_tor: bool = False
    tor_proxy: str = "socks5://127.0.0.1:9050"
    
    # DNS
    enable_dns_encryption: bool = True
    dns_servers: List[str] = field(default_factory=lambda: ["1.1.1.1", "9.9.9.9"])
    use_doh: bool = False  # P16: DNS-over-HTTPS via resolve_doh before fetch
    
    # Encryption
    enable_encryption: bool = True
    encryption_algorithm: str = "fernet"  # fernet, aes256


@dataclass
class DeepResearchConfig:
    """Deep research configuration"""
    # Exploration
    max_depth: int = 10
    strategy: str = "hybrid"  # depth_first, breadth_first, citation, tangent, hybrid
    follow_citations: bool = True
    explore_tangents: bool = True
    
    # Limits
    max_threads: int = 5
    max_documents: int = 1000
    max_citations_per_doc: int = 20
    
    # Citation types
    citation_types: List[str] = field(default_factory=lambda: [
        "academic", "patent", "preprint", "dataset"
    ])
    
    # Auto-summarization
    enable_auto_summarize: bool = True
    summarization_model: str = "qwen3-1.7b"


# =============================================================================
# UNIFIED CONFIGURATION
# =============================================================================

@dataclass
class UniversalConfig:
    """
    Unified configuration for the Universal Orchestrator.
    
    This class consolidates all configuration options from:
    - Research execution (ResearchConfig)
    - Memory management (MemoryConfig)
    - Ghost operations (GhostConfig)
    - Coordination (CoordinationConfig)
    - Agent management (AgentManagerConfig)
    """
    
    # Mode
    mode: ResearchMode = ResearchMode.STANDARD
    
    # Sub-configurations
    research: ResearchConfig = field(default_factory=ResearchConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    ghost: GhostConfig = field(default_factory=GhostConfig)
    coordination: CoordinationConfig = field(default_factory=CoordinationConfig)
    agent_manager: AgentManagerConfig = field(default_factory=AgentManagerConfig)
    
    # Extended configurations (NEW)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    stealth: StealthConfig = field(default_factory=StealthConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    deep_research: DeepResearchConfig = field(default_factory=DeepResearchConfig)
    communication: CommunicationConfig = field(default_factory=CommunicationConfig)
    
    # Paths
    db_path: Optional[str] = None
    vault_path: Optional[str] = None
    models_dir: Optional[str] = None
    
    # Feature flags
    enable_ghost_layer: bool = True
    enable_coordination_layer: bool = True
    enable_knowledge_layer: bool = False  # Disabled by default (RAM)
    enable_rag_pipeline: bool = False  # Disabled by default (RAM)
    enable_reasoning_engine: bool = True
    
    # Extended feature flags (NEW)
    enable_security_layer: bool = True
    enable_stealth_layer: bool = True
    enable_privacy_layer: bool = False  # Disabled by default (VPN/Tor required)
    enable_deep_research: bool = True
    enable_communication_layer: bool = True
    
    # Logging
    log_level: str = "INFO"
    log_to_file: bool = True
    log_dir: str = "logs"
    
    # M1 optimization
    m1_optimized: bool = True
    context_swap_enabled: bool = True
    enable_thermal_management: bool = True

    # MoE (Mixture-of-Experts) configuration
    enable_moe_router: bool = True  # Enable MoE brain upgrade
    enable_moe_synthesis: bool = True  # Use MoE for synthesis phase
    moe_max_active_experts: int = 2  # M1 8GB limit

    # Neuromorphic SNN configuration
    enable_neuromorphic: bool = True  # Enable SNN prioritization
    snn_n_neurons: int = 500  # Number of neurons (M1 8GB optimized)
    snn_connection_prob: float = 0.05  # Sparse connectivity
    snn_enable_stdp: bool = True  # Enable STDP learning

    # Federated Learning configuration
    enable_federated_osint: bool = False  # Disabled by default (privacy)
    federated_dp_epsilon: float = 0.1  # Differential privacy budget
    federated_batch_size: int = 16  # M1 8GB limit
    federated_round_interval_hours: int = 24

    # Quantum Pathfinding configuration
    enable_quantum_pathfinding: bool = True
    quantum_max_steps: int = 50
    quantum_amplification_strength: float = 1.5
    quantum_max_nodes: int = 5000

    # Distillation Engine configuration
    enable_distillation: bool = False
    distillation_hidden_dim: int = 128
    distillation_learning_rate: float = 0.001

    # Agent Meta-Optimizer configuration
    enable_agent_meta_optimization: bool = True
    agent_meta_optimization_interval: int = 10
    agent_meta_min_samples: int = 5

    # Tree of Thoughts configuration
    tot_enabled: bool = True  # Enable autonomous ToT activation
    tot_complexity_threshold: float = 0.70  # Threshold for ToT activation
    tot_hybrid_threshold: float = 0.45  # Threshold for hybrid ToT+MoE mode
    tot_max_depth: int = 5  # Maximum tree depth
    tot_max_time: float = 120.0  # Maximum ToT execution time in seconds
    tot_enable_backtracking: bool = True  # Enable backtracking in ToT
    tot_enable_mcts: bool = True  # Enable Monte Carlo Tree Search

    # Phase 6: Steganography Detection configuration
    enable_steganography_detection: bool = True
    stego_chi_square_threshold: float = 0.05
    stego_rs_analysis_enabled: bool = True
    stego_dct_analysis_enabled: bool = True
    stego_max_image_size: int = 2048

    # Phase 6: DNS Tunneling Detection configuration
    enable_dns_tunnel_detection: bool = True
    dns_entropy_threshold: float = 4.2
    dns_ngram_threshold: float = 0.7
    dns_lstm_threshold: float = 0.8
    dns_max_queries_per_batch: int = 1000
    dns_enable_lstm: bool = True
    dns_pcap_chunk_seconds: int = 60

    # Phase 6: Unicode Attack Analysis configuration
    enable_unicode_attack_detection: bool = True
    unicode_detect_zero_width: bool = True
    unicode_detect_homoglyphs: bool = True
    unicode_detect_bidi_attacks: bool = True
    unicode_detect_normalization: bool = True
    unicode_chunk_size: int = 1048576  # 1MB

    # Phase 7: Metadata Extraction configuration
    enable_metadata_extraction: bool = True
    metadata_extract_exif: bool = True
    metadata_extract_gps: bool = True
    metadata_reverse_geocode: bool = False
    metadata_extract_audio: bool = True
    metadata_extract_video: bool = False
    metadata_calculate_hashes: bool = True
    metadata_hash_algorithms: List[str] = field(default_factory=lambda: ["md5", "sha256"])
    metadata_max_file_size: int = 1073741824  # 1GB
    metadata_batch_size: int = 100

    # Phase 8: Text Analysis Suite - Encoding Detection
    enable_encoding_detection: bool = True
    encoding_min_length: int = 20
    encoding_detect_nested: bool = True
    encoding_max_depth: int = 5
    encoding_chunk_size: int = 1048576  # 1MB

    # Phase 8: Text Analysis Suite - Hash Identification
    enable_hash_identification: bool = True
    hash_min_confidence: float = 0.3
    hash_top_k_results: int = 3
    hash_detect_salted: bool = True
    hash_batch_size: int = 1000

    # Phase 9: Autonomous Intelligence Layer
    enable_autonomous_intelligence: bool = True
    intelligence_decision_threshold: float = 0.3
    intelligence_max_parallel_modules: int = 4
    intelligence_module_timeout: int = 60  # seconds
    intelligence_enable_learning: bool = True
    intelligence_cache_results: bool = True
    intelligence_cache_ttl: int = 3600  # 1 hour

    # Analysis modes
    analysis_mode_default: str = "auto"  # auto, quick, deep
    quick_scan_time_limit: int = 5  # seconds
    deep_analysis_modules: List[str] = field(default_factory=list)  # empty = all relevant

    @classmethod
    def for_mode(cls, mode: ResearchMode, m1_optimized: bool = True) -> UniversalConfig:
        """
        Create configuration optimized for specific research mode.
        
        Args:
            mode: Research mode (QUICK, STANDARD, DEEP, EXTREME, AUTONOMOUS)
            m1_optimized: Whether to apply M1 8GB optimizations
            
        Returns:
            Configured UniversalConfig instance
        """
        # Get preset values
        preset = ResearchPresets.get_preset(mode)
        
        # Create base config
        config = cls(
            mode=mode,
            enable_knowledge_layer=mode in [ResearchMode.DEEP, ResearchMode.EXTREME, ResearchMode.AUTONOMOUS],
            enable_rag_pipeline=mode in [ResearchMode.STANDARD, ResearchMode.DEEP, ResearchMode.EXTREME, ResearchMode.AUTONOMOUS],
            m1_optimized=m1_optimized,
        )
        
        # Apply preset values to research config
        config.research.mode = mode
        config.research.max_steps = preset.get("max_steps", 20)
        config.research.max_time_minutes = preset.get("max_time_minutes", 30)
        config.research.max_concurrent_agents = preset.get("max_concurrent_agents", 3)
        config.research.enable_knowledge_graph = preset.get("enable_knowledge_graph", False)
        config.research.enable_rag = preset.get("enable_rag", True)
        config.research.enable_fact_checking = preset.get("enable_fact_checking", False)
        config.research.save_intermediate = preset.get("save_intermediate", False)
        
        # Apply M1 optimizations
        if m1_optimized:
            config._apply_m1_optimizations()
        
        return config
    
    def _apply_m1_optimizations(self) -> None:
        """Apply M1 8GB RAM optimizations"""
        # Memory limits
        self.memory.memory_limit_mb = M1Presets.MEMORY_LIMIT_MB
        self.memory.thermal_threshold_c = M1Presets.THERMAL_THRESHOLD_C
        
        # Models - 3 model stack only
        self.research.hermes_model = M1Presets.HERMES_MODEL
        self.research.modernbert_model = M1Presets.MODERNBERT_MODEL
        self.research.gliner_model = M1Presets.GLINER_MODEL
        
        # Agent management
        self.agent_manager.max_concurrent_agents = min(
            self.agent_manager.max_concurrent_agents,
            M1Presets.MAX_CONCURRENT_AGENTS
        )
        self.agent_manager.agent_timeout_seconds = M1Presets.AGENT_TIMEOUT_SECONDS
        self.agent_manager.circuit_breaker_threshold = M1Presets.CIRCUIT_BREAKER_THRESHOLD
        
        # Disable heavy features if not enough RAM
        if self.research.max_concurrent_agents > 4:
            self.enable_knowledge_layer = False
        
        # Coordination
        self.coordination.max_context_length = 1024  # Minimal for M1
        self.coordination.temperature = 0.1  # Consistent decisions

        # Quantum pathfinding M1 limits
        self.quantum_max_nodes = min(self.quantum_max_nodes, 5000)

        # Distillation M1 limits
        self.distillation_hidden_dim = min(self.distillation_hidden_dim, 128)
    
    @classmethod
    def from_env(cls) -> UniversalConfig:
        """
        Load configuration from environment variables.
        
        Supported variables:
        - HLEDAC_RESEARCH_MODE: quick, standard, deep, extreme, autonomous
        - HLEDAC_MEMORY_LIMIT_MB: Memory limit in MB
        - HLEDAC_MAX_STEPS: Maximum research steps
        - HLEDAC_LOG_LEVEL: DEBUG, INFO, WARNING, ERROR
        - HLEDAC_M1_OPTIMIZED: true/false
        """
        # Determine mode
        mode_str = os.getenv("HLEDAC_RESEARCH_MODE", "standard").upper()
        try:
            mode = ResearchMode[mode_str]
        except KeyError:
            mode = ResearchMode.STANDARD
        
        # Create config
        m1_optimized = os.getenv("HLEDAC_M1_OPTIMIZED", "true").lower() == "true"
        config = cls.for_mode(mode, m1_optimized)
        
        # Override from env
        if memory_limit := os.getenv("HLEDAC_MEMORY_LIMIT_MB"):
            config.memory.memory_limit_mb = float(memory_limit)
        
        if max_steps := os.getenv("HLEDAC_MAX_STEPS"):
            config.research.max_steps = int(max_steps)
        
        if log_level := os.getenv("HLEDAC_LOG_LEVEL"):
            config.log_level = log_level
        
        return config
    
    def update(self, **kwargs) -> None:
        """
        Update configuration values.
        
        Example:
            config.update(max_steps=50, enable_stealth=False)
        """
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
            # Extended configs (NEW)
            elif hasattr(self.security, key):
                setattr(self.security, key, value)
            elif hasattr(self.stealth, key):
                setattr(self.stealth, key, value)
            elif hasattr(self.privacy, key):
                setattr(self.privacy, key, value)
            elif hasattr(self.deep_research, key):
                setattr(self.deep_research, key, value)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        return {
            "mode": self.mode.value,
            "research": self.research.__dict__,
            "memory": self.memory.__dict__,
            "ghost": self.ghost.__dict__,
            "coordination": self.coordination.__dict__,
            "agent_manager": self.agent_manager.__dict__,
            # Extended configs (NEW)
            "security": self.security.__dict__,
            "stealth": self.stealth.__dict__,
            "privacy": self.privacy.__dict__,
            "deep_research": self.deep_research.__dict__,
            # Feature flags
            "enable_ghost_layer": self.enable_ghost_layer,
            "enable_coordination_layer": self.enable_coordination_layer,
            "enable_knowledge_layer": self.enable_knowledge_layer,
            "enable_rag_pipeline": self.enable_rag_pipeline,
            "enable_security_layer": self.enable_security_layer,
            "enable_stealth_layer": self.enable_stealth_layer,
            "enable_privacy_layer": self.enable_privacy_layer,
            "enable_deep_research": self.enable_deep_research,
            "enable_communication_layer": self.enable_communication_layer,
            # Quantum Pathfinding
            "enable_quantum_pathfinding": self.enable_quantum_pathfinding,
            "quantum_max_steps": self.quantum_max_steps,
            "quantum_amplification_strength": self.quantum_amplification_strength,
            "quantum_max_nodes": self.quantum_max_nodes,
            # Distillation Engine
            "enable_distillation": self.enable_distillation,
            "distillation_hidden_dim": self.distillation_hidden_dim,
            "distillation_learning_rate": self.distillation_learning_rate,
            # Agent Meta-Optimizer
            "enable_agent_meta_optimization": self.enable_agent_meta_optimization,
            "agent_meta_optimization_interval": self.agent_meta_optimization_interval,
            "agent_meta_min_samples": self.agent_meta_min_samples,
            "m1_optimized": self.m1_optimized,
        }
    
    def validate(self) -> List[str]:
        """
        Validate configuration and return list of issues.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        issues = []
        
        # Memory validation
        if self.memory.memory_limit_mb > 6000:
            issues.append("Memory limit exceeds safe M1 8GB threshold (6000MB)")
        
        if self.memory.memory_limit_mb < 2000:
            issues.append("Memory limit too low for meaningful operation")
        
        # Research validation
        if self.research.max_steps < 1:
            issues.append("max_steps must be at least 1")
        
        if self.research.max_time_minutes < 1:
            issues.append("max_time_minutes must be at least 1")
        
        # Agent validation
        if self.agent_manager.max_concurrent_agents > 10:
            issues.append("max_concurrent_agents > 10 may cause memory issues")
        
        # M1 optimization warnings
        if self.m1_optimized:
            if self.enable_knowledge_layer and self.agent_manager.max_concurrent_agents > 4:
                issues.append("Warning: Knowledge layer with many agents may exceed M1 RAM")
            
            if self.enable_rag_pipeline and self.enable_knowledge_layer:
                issues.append("Warning: RAG + Knowledge layer may exceed M1 RAM")
        
        return issues


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_config(
    mode: ResearchMode = ResearchMode.STANDARD,
    m1_optimized: bool = True,
    **overrides
) -> UniversalConfig:
    """
    Create configuration with optional overrides.
    
    Example:
        config = create_config(
            mode=ResearchMode.DEEP,
            max_steps=50,
            enable_stealth=True
        )
    """
    config = UniversalConfig.for_mode(mode, m1_optimized)
    config.update(**overrides)
    return config


def load_config_from_file(path: str) -> UniversalConfig:
    """
    Load configuration from JSON file.

    Args:
        path: Path to configuration file (.json)

    Returns:
        UniversalConfig instance
    """
    import json
    
    path = Path(path)
    
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    
    with open(path) as f:
        if path.suffix == ".json":
            data = json.load(f)
        else:
            raise ValueError(f"Unsupported config format: {path.suffix}")
    
    # Parse mode
    mode = ResearchMode(data.get("mode", "standard"))
    m1_optimized = data.get("m1_optimized", True)
    
    # Create config
    config = UniversalConfig.for_mode(mode, m1_optimized)
    
    # Apply overrides
    config.update(**{k: v for k, v in data.items() if k not in ["mode", "m1_optimized"]})
    
    return config
