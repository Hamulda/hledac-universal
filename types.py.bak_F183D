"""
Universal Orchestrator Types - Consolidated Type Definitions
=============================================================

All enums and dataclasses used across the universal orchestrator.
Consolidated from:
- orchestrator_v2.py (ResearchMode, OrchestratorState, ActionType, etc.)
- supreme/orchestrator.py (SystemState variants)
- hermes3/types.py (DecisionRequest, DecisionResponse)
- deepseek_r1/types.py (OperationType)
- m1_master_optimizer/ (SystemState)
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from .autonomous_analyzer import AutoResearchProfile

    import numpy as np  # noqa: F401 — type annotations only


# =============================================================================
# RESEARCH ENUMS
# =============================================================================

class ResearchMode(Enum):
    """Research depth modes"""
    QUICK = "quick"           # Fast, shallow research
    STANDARD = "standard"     # Balanced depth
    DEEP = "deep"             # Deep investigation
    EXTREME = "extreme"       # Exhaustive research
    AUTONOMOUS = "autonomous" # Self-directed research


class ActionResultType(Enum):
    """Strict typed handler result taxonomy for truthful benchmark."""
    SUCCESS = "SUCCESS"                   # Action completed with valid results
    EMPTY = "EMPTY"                       # Action completed but no results found
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"  # Network unreachable / DNS / connection refused
    UPSTREAM_API_ERROR = "UPSTREAM_API_ERROR"   # HTTP 429/403/451/502/503/504/529
    TIMEOUT = "TIMEOUT"                   # Action timed out
    EXCEPTION = "EXCEPTION"               # Unhandled exception / code bug
    MOCK_FALLBACK_USED = "MOCK_FALLBACK_USED"   # Fixture/mock data used


class OfflineModeError(Exception):
    """Raised when network operations are attempted in offline mode."""
    pass


def is_offline_mode() -> bool:
    """Check if offline mode is enabled via HLEDAC_OFFLINE environment variable."""
    return os.getenv("HLEDAC_OFFLINE", "0") == "1"


class OrchestratorState(Enum):
    """Main orchestrator state machine states"""
    IDLE = "idle"
    PLANNING = "planning"
    BRAIN = "brain"
    EXECUTION = "execution"
    SYNTHESIS = "synthesis"
    ERROR = "error"


class SystemState(Enum):
    """System health state machine (from InfrastructureOrchestrator)"""
    HEALTHY = "healthy"
    MEMORY_PRESSURE = "memory_pressure"
    THERMAL_THROTTLING = "thermal_throttling"
    DEGRADED = "degraded"
    RECOVERY = "recovery"


class AgentState(Enum):
    """Sub-agent states"""
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    LOST = "lost"  # Agent lost direction, needs redirection


class SubAgentType(Enum):
    """Types of sub-agents"""
    STEALTH_WEB = "stealth_web"    # Web crawling with TLS fingerprinting
    OSINT = "osint"                 # Hidden sources discovery
    SECURITY = "security"           # Obfuscation, audit
    ARCHIVE = "archive"             # Wayback Machine, archives
    ACADEMIC = "academic"           # Research papers
    SYNTHESIS = "synthesis"         # Result synthesis


class Severity(Enum):
    """Severity levels for logging and alerts"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class SecurityLevel(Enum):
    """Security levels for privacy protection"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionType(Enum):
    """GhostDirector action types (18+ actions)"""
    # Core actions
    SCAN = "scan"
    GOOGLE = "google"
    DOWNLOAD = "download"
    SEARCH = "search"
    SMART_SEARCH = "smart_search"
    MEMORIZE = "memorize"
    PROBE = "probe"
    TRACK = "track"
    RESEARCH_PAPER = "research_paper"
    DEEP_RESEARCH = "deep_research"
    DEEP_READ = "deep_read"
    ANSWER = "answer"
    CRACK = "crack"
    ERROR = "error"
    
    # Extended actions
    ARCHIVE_FALLBACK = "archive_fallback"
    FACT_CHECK = "fact_check"
    STEALTH_HARVEST = "stealth_harvest"
    OSINT_DISCOVERY = "osint_discovery"
    EXTRACT_ENTITIES = "extract_entities"
    ANALYZE_SENTIMENT = "analyze_sentiment"
    SUMMARIZE = "summarize"


class OperationType(Enum):
    """Operation types for coordinator delegation"""
    RESEARCH = "research"
    SECURITY = "security"
    EXECUTION = "execution"
    MONITORING = "monitoring"
    ANALYSIS = "analysis"
    SYNTHESIS = "synthesis"


class ResearchPhase(Enum):
    """Research execution phases"""
    INITIALIZATION = "initialization"
    EXPLORATION = "exploration"
    DEEP_DIVE = "deep_dive"
    ANALYSIS = "analysis"
    SYNTHESIS = "synthesis"
    FINALIZATION = "finalization"


class QueryComplexity(Enum):
    """Query complexity levels (from MODOrchestrator)"""
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    VERY_COMPLEX = "very_complex"


class ReasoningMode(Enum):
    """Reasoning modes for autonomous orchestration"""
    STANDARD = "standard"
    CHAIN_OF_THOUGHT = "chain_of_thought"
    TREE_OF_THOUGHTS = "tree_of_thoughts"
    HYBRID_TOT_MOE = "hybrid_tot_moe"


# =============================================================================
# DATACLASSES - CONFIGURATION
# =============================================================================

@dataclass
class ModelConfig:
    """Model configuration for M1 8GB - 3 model stack only"""
    # LLM: Hermes-3 for reasoning and generation
    HERMES_MODEL: str = "mlx-community/Hermes-3-Llama-3.2-3B-4bit"
    HERMES_CONTEXT: int = 8192
    HERMES_TEMP: float = 0.3

    # Embeddings: ModernBERT for semantic search
    MODERNBERT_MODEL: str = "mlx-community/answerdotai-ModernBERT-base-6bit"
    EMBED_DIM: int = 768

    # NER: GLiNER-X for entity extraction
    GLINER_MODEL: str = "knowledgator/gliner-x-base"


@dataclass
class ResearchConfig:
    """Research execution configuration"""
    mode: ResearchMode = ResearchMode.STANDARD
    max_steps: int = 20
    max_time_minutes: int = 30
    memory_limit_mb: float = 5500.0
    
    # Models - 3 model stack only
    hermes_model: str = ModelConfig.HERMES_MODEL
    modernbert_model: str = ModelConfig.MODERNBERT_MODEL
    gliner_model: str = ModelConfig.GLINER_MODEL

    # Knowledge (optional - no Neo4j)
    enable_knowledge_graph: bool = False
    enable_rag: bool = True
    db_path: Optional[str] = None
    
    # Stealth
    enable_stealth: bool = True
    auto_stealth: bool = True
    privacy_level: str = "high"
    chaff_ratio: float = 0.3
    enable_audit: bool = True
    
    # Autonomy
    enable_autonomy: bool = True
    auto_archive_fallback: bool = True
    enable_fact_checking: bool = True
    
    # Output
    output_format: str = "markdown"
    save_intermediate: bool = True
    
    # Security
    use_ram_vault: bool = True
    vault_password: Optional[str] = None
    
    # Sub-agents
    max_concurrent_agents: int = 3
    agent_timeout: int = 300


@dataclass
class MemoryConfig:
    """Memory management configuration (from InfrastructureOrchestrator)"""
    memory_limit_mb: float = 5500.0
    thermal_threshold_c: float = 85.0
    enable_secure_enclave: bool = True
    enable_metal_acceleration: bool = True
    recovery_interval_seconds: float = 30.0
    health_check_interval_seconds: float = 5.0


@dataclass
class GhostConfig:
    """Ghost layer configuration"""
    max_steps: int = 20
    enable_vault: bool = True
    vault_size_mb: int = 256
    enable_anti_loop: bool = True
    stagnation_threshold: int = 3
    enable_loot_manager: bool = True


@dataclass
class SecurityConfig:
    """Security configuration for privacy protection"""
    # Basic
    enable_audit: bool = True
    privacy_level: str = "high"
    use_ram_vault: bool = True
    vault_password: Optional[str] = None
    pii_detection: bool = True
    auto_redact: bool = True
    # Obfuscation (required by SecurityLayer)
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


@dataclass
class StealthConfig:
    """Stealth mode configuration"""
    # Basic
    enabled: bool = True
    chaff_ratio: float = 0.3
    rotate_identity: bool = True
    use_tor: bool = False
    use_proxy: bool = False
    proxy_url: Optional[str] = None
    timing_jitter: bool = True
    user_agent_rotation: bool = True
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
    # Anti-detection extras
    hide_webdriver: bool = True
    hide_automation: bool = True
    spoof_plugins: bool = True
    spoof_permissions: bool = True
    disable_webrtc: bool = True
    override_canvas: bool = True
    override_webgl: bool = True
    spoof_fonts: bool = True
    emulate_human_events: bool = True
    patch_detection_libs: bool = True
    randomize_globals: bool = True
    spoof_chrome_runtime: bool = True
    add_chrome_plugins: bool = False
    # OCR
    enable_image_ocr: bool = False
    ocr_model: str = "microsoft/trocr-base-handwritten"
    max_image_size: int = 2048
    confidence_threshold: float = 0.5
    # Timezone/Fonts
    randomize_timezone: bool = True
    randomize_webgl: bool = True
    randomize_fonts: bool = True
    randomize_plugins: bool = True
    consistent_per_session: bool = True
    session_duration: int = 300
    platform: str = "macos"
    # Pattern
    pattern: str = "default"
    min_delay: float = 0.1
    max_delay: float = 0.5
    randomness: float = 0.3
    mouse_speed: float = 1.0
    scroll_min: int = 20
    scroll_max: int = 50
    scroll_pause: float = 0.2


@dataclass
class CoordinationConfig:
    """Coordination layer configuration"""
    max_context_length: int = 1024  # Minimal context for M1 optimization
    temperature: float = 0.1  # Low for consistent decisions
    max_tokens_response: int = 100
    enable_delegation: bool = True


@dataclass
class AgentManagerConfig:
    """Agent management configuration (from EnhancedUnifiedOrchestrator)"""
    max_concurrent_agents: int = 6  # M1 constraint
    memory_threshold_mb: float = 512.0
    agent_timeout_seconds: float = 25.0
    circuit_breaker_threshold: int = 3
    agent_pool_size: int = 2
    auto_optimize_interval: int = 300  # 5 minutes


# =============================================================================
# DATACLASSES - EXECUTION CONTEXT
# =============================================================================

@dataclass
class ExecutionContext:
    """Context for research execution (from v1 + v2)"""
    query: str
    current_step: int = 0
    max_steps: int = 20
    state: OrchestratorState = OrchestratorState.IDLE
    
    # History
    execution_history: List[Dict[str, Any]] = field(default_factory=list)
    action_log: List[Dict[str, Any]] = field(default_factory=list)
    
    # Knowledge
    collected_data: List[Dict[str, Any]] = field(default_factory=list)
    knowledge_graph: Dict[str, Any] = field(default_factory=dict)
    
    # Stealth
    stealth_activated: bool = False
    blocked_domains: Set[str] = field(default_factory=set)
    
    # Deduplication
    visited_urls: Set[str] = field(default_factory=set)
    content_hashes: Set[str] = field(default_factory=set)
    
    # Statistics
    start_time: float = field(default_factory=lambda: datetime.now().timestamp())
    tokens_used: int = 0
    
    def add_action(self, action_type: ActionType, details: Dict[str, Any]) -> None:
        """Add action to log"""
        self.action_log.append({
            "step": self.current_step,
            "action": action_type.value,
            "timestamp": datetime.now().isoformat(),
            "details": details,
        })


@dataclass
class DecisionContext:
    """Context for decision making (from Hermes3)"""
    research_id: str
    goal: str
    phase: ResearchPhase
    iterations: int = 0
    max_iterations: int = 20
    context_data: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# DATACLASSES - RESULTS
# =============================================================================

@dataclass
class SubAgentResult:
    """Result from sub-agent execution"""
    agent_type: SubAgentType
    success: bool
    data: Dict[str, Any]
    confidence: float
    sources: List[Dict[str, Any]]
    execution_time: float
    state: AgentState


@dataclass
class ResearchResult:
    """Final research result"""
    success: bool
    query: str
    mode: ResearchMode
    final_answer: str
    sources: List[Dict[str, Any]] = field(default_factory=list)
    knowledge_graph: Dict[str, Any] = field(default_factory=dict)
    execution_history: List[Dict[str, Any]] = field(default_factory=list)
    agent_results: List[SubAgentResult] = field(default_factory=list)
    statistics: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_markdown(self) -> str:
        """Export result as Markdown"""
        lines = [
            f"# Research Report: {self.query}",
            f"",
            f"**Mode:** {self.mode.value}",
            f"**Success:** {'✅' if self.success else '❌'}",
            f"**Sources:** {len(self.sources)}",
            f"**Agents Used:** {len([r for r in self.agent_results if r.success])}",
            f"",
            f"## Answer",
            f"",
            self.final_answer,
            f"",
            f"## Sources",
            f"",
        ]
        
        for i, source in enumerate(self.sources, 1):
            lines.append(f"{i}. [{source.get('title', 'Unknown')}]({source.get('url', '#')})")
        
        if self.statistics:
            lines.extend([
                f"",
                f"## Statistics",
                f"",
                f"```json",
                f"{self._dict_to_json(self.statistics)}",
                f"```",
            ])
        
        return "\n".join(lines)
    
    @staticmethod
    def _dict_to_json(d: Dict) -> str:
        """Simple dict to JSON string"""
        import json
        return json.dumps(d, indent=2, default=str)


@dataclass
class DecisionRequest:
    """Request for decision making (from DeepSeek R1)"""
    operation_type: OperationType
    context: Dict[str, Any]
    priority: int = 5  # 1-10
    timeout_seconds: float = 30.0
    requires_delegation: bool = True


@dataclass
class DecisionResponse:
    """Response from decision making"""
    decision_id: str
    operation_type: OperationType
    action: str
    parameters: Dict[str, Any]
    confidence: float
    coordinator_id: Optional[str] = None
    reasoning: Optional[str] = None


@dataclass
class ActionResult:
    """Result from Ghost action execution"""
    action: ActionType
    success: bool
    data: Dict[str, Any]
    execution_time: float
    stagnation_detected: bool = False
    stored_in_vault: bool = False


@dataclass
class SystemMetrics:
    """System health metrics (from InfrastructureOrchestrator)"""
    memory_used_mb: float
    memory_available_mb: float
    cpu_percent: float
    temperature_c: Optional[float]
    state: SystemState
    timestamp: float


@dataclass
class AgentMetrics:
    """Agent performance metrics"""
    agent_type: SubAgentType
    success_rate: float
    avg_execution_time: float
    circuit_breaker_open: bool
    consecutive_failures: int
    total_executions: int


@dataclass
class ComplexityAnalysis:
    """Complexity analysis result for ToT decision making"""
    score: float
    requires_multi_step: bool
    estimated_depth: int
    tot_recommended: bool
    indicators: Dict[str, float]


# =============================================================================
# ANALYZER RESULT (Sprint 8SD: CapabilityRouter Bridge)
# =============================================================================

@dataclass
class AnalyzerResult:
    """
    Structured output from AutonomousAnalyzer.

    Canonical form for the analyzer -> capability router -> tool registry pipeline.
    Wraps AutoResearchProfile for typed capability routing.

    NOTE: This is a bridge type. The underlying AutoResearchProfile remains
    the source of truth for analyzer output until full migration.
    """
    # Tool routing
    tools: Set[str] = field(default_factory=set)

    # Source routing
    sources: Set[str] = field(default_factory=set)

    # Privacy configuration
    privacy_level: str = "STANDARD"
    use_tor: bool = False

    # Model requirements (for ModelLifecycleManager)
    models_needed: Set[str] = field(default_factory=set)

    # Execution parameters
    depth: str = "STANDARD"
    max_time: float = 300.0

    # ToT configuration
    use_tot: bool = False
    tot_mode: str = "standard"

    # Reasoning trace
    reasoning: str = ""

    # Raw profile reference (for backward compatibility during transition)
    _raw_profile: Optional[Any] = field(default=None, repr=False)

    @classmethod
    def from_profile(cls, profile: "AutoResearchProfile") -> "AnalyzerResult":
        """
        Create AnalyzerResult from AutoResearchProfile.

        This is an adapter bridge - the AutoResearchProfile is preserved
        in _raw_profile for backward compatibility.
        """
        return cls(
            tools=profile.tools.copy(),
            sources=profile.sources.copy(),
            privacy_level=profile.privacy_level,
            use_tor=profile.use_tor,
            models_needed=profile.models_needed.copy(),
            depth=profile.depth,
            max_time=profile.max_time,
            use_tot=profile.use_tot,
            tot_mode=profile.tot_mode,
            reasoning=profile.reasoning,
            _raw_profile=profile,
        )

    def to_capability_signal(self) -> Dict[str, Any]:
        """
        Convert to capability signal for CapabilityRouter.

        Returns a typed dict that CapabilityRouter.route() can process.
        """
        return {
            "tools": self.tools,
            "sources": self.sources,
            "privacy_level": self.privacy_level,
            "use_tor": self.use_tor,
            "depth": self.depth,
            "use_tot": self.use_tot,
            "tot_mode": self.tot_mode,
            "requires_embeddings": bool(self.models_needed & {"modernbert"}),
            "requires_ner": bool(self.models_needed & {"gliner"}),
            "requires_temporal": "temporal_analyzer" in self.tools,
            "requires_crypto": "blockchain_analyzer" in self.tools,
        }


# =============================================================================
# TYPE ALIASES
# =============================================================================

# For backwards compatibility
AgentCapability = Dict[str, Any]
TaskDefinition = Dict[str, Any]
PlanStep = Dict[str, Any]
KnowledgeNode = Dict[str, Any]


# =============================================================================
# EXCEPTIONS
# =============================================================================

class OrchestratorError(Exception):
    """Base orchestrator error"""
    pass


class StagnationError(OrchestratorError):
    """Detected stagnation/loop in research"""
    pass


class MemoryPressureError(OrchestratorError):
    """Memory limit exceeded"""
    pass


class CircuitBreakerOpenError(OrchestratorError):
    """Circuit breaker is open for agent"""
    pass


class RateLimitExceeded(OrchestratorError):
    """Rate limit exceeded"""
    pass


# =============================================================================
# BASE ORCHESTRATOR CLASS
# =============================================================================

class UniversalResearchOrchestrator:
    """
    Base class for universal research orchestrators.

    Provides common interface and base functionality for all orchestrators
    in the Hledac universal system.

    This is an abstract base class - concrete implementations should
    override the research method.
    """

    def __init__(self, config: Optional[ResearchConfig] = None):
        """
        Initialize the orchestrator.

        Args:
            config: Research configuration
        """
        self.config = config or ResearchConfig()
        self.state = OrchestratorState.IDLE
        self._initialized = False

    async def initialize(self) -> bool:
        """
        Initialize the orchestrator and all subsystems.

        Returns:
            True if initialization successful
        """
        self._initialized = True
        return True

    async def research(
        self,
        query: str,
        search_func: Optional[Any] = None,
        domain: str = "general"
    ) -> Any:
        """
        Execute research query.

        Args:
            query: Research query
            search_func: Optional search function
            domain: Domain context

        Returns:
            Research results

        Raises:
            NotImplementedError: Must be implemented by subclasses
        """
        raise NotImplementedError("Subclasses must implement research()")

    async def cleanup(self) -> None:
        """Cleanup resources."""
        self._initialized = False
        self.state = OrchestratorState.IDLE

    def get_stats(self) -> Dict[str, Any]:
        """Get orchestrator statistics."""
        return {
            "state": self.state.value,
            "initialized": self._initialized,
        }


# =============================================================================
# SECURITY & CRYPTOGRAPHY ENUMS (NEW)
# =============================================================================

class ObfuscationLevel(Enum):
    """String/content obfuscation levels"""
    NONE = "none"
    LIGHT = "light"      # Simple encoding
    MEDIUM = "medium"    # Multi-stage encoding
    HEAVY = "heavy"      # Full obfuscation with decoys
    MAXIMUM = "maximum"  # Military-grade obfuscation


class WipeStandard(Enum):
    """Secure data destruction standards"""
    NIST_800_88 = "nist_800_88"      # 1 pass random
    DoD_5220_22M = "dod_5220_22m"    # 3 passes (0x00, 0xFF, random)
    GUTMANN = "gutmann"               # 35 passes (overkill)


class RiskLevel(Enum):
    """Detection risk levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BrowserType(Enum):
    """Browser types for stealth"""
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class CaptchaType(Enum):
    """CAPTCHA types"""
    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    FUNCAPTCHA = "funcaptcha"
    IMAGE = "image"
    GEETEST = "geetest"


class PrivacyLevel(Enum):
    """Privacy protection levels"""
    NONE = "none"
    BASIC = "basic"          # DNS encryption only
    STANDARD = "standard"    # VPN + DNS
    ENHANCED = "enhanced"    # VPN + Tor + DNS
    MAXIMUM = "maximum"      # Multi-hop + Tor


class ExplorationStrategy(Enum):
    """Deep research exploration strategies"""
    DEPTH_FIRST = "depth_first"           # Follow one chain deeply
    BREADTH_FIRST = "breadth_first"       # Explore all levels equally
    CITATION_FOLLOWING = "citation"       # Focus on academic citations
    TANGENT_EXPLORATION = "tangent"       # Follow related topics
    HYBRID = "hybrid"                     # Combine multiple strategies


class CommunicationPattern(Enum):
    """Protocol communication patterns"""
    REQUEST_RESPONSE = "request_response"
    STREAMING = "streaming"
    PUB_SUB = "pub_sub"


class LeakSource(Enum):
    """Data leak sources"""
    BREACH_DATABASE = "breach_database"
    DARK_WEB = "dark_web"
    PASTE_SITE = "paste_site"
    SOCIAL_MEDIA = "social_media"
    PUBLIC_RECORDS = "public_records"


class ContentSource(Enum):
    """Archive content sources"""
    WAYBACK = "wayback"
    SEARCH_CACHE = "search_cache"
    SOCIAL_ARCHIVE = "social_archive"


# =============================================================================
# DATACLASSES - SECURITY & CRYPTO (NEW)
# =============================================================================

@dataclass
class ObfuscationResult:
    """Result of string obfuscation"""
    original_hash: str
    obfuscated_data: str
    encoding_chain: List[str]  # e.g., ["xor", "base64", "zlib"]
    decoy_count: int
    success: bool


@dataclass
class DestructionResult:
    """Result of secure data destruction"""
    file_path: str
    standard: WipeStandard
    passes_completed: int
    bytes_overwritten: int
    verification_passed: bool
    timestamp: float


@dataclass
class StealthSession:
    """Stealth browsing session"""
    session_id: str
    browser_type: BrowserType
    fingerprint: Dict[str, Any]
    proxy: Optional[str]
    risk_level: RiskLevel
    created_at: float


@dataclass
class CaptchaSolution:
    """CAPTCHA solving result"""
    solution: str
    solved_at: float
    cost: float
    confidence: float
    provider: str


@dataclass
class PrivacyStatus:
    """Current privacy/anonymity status"""
    vpn_connected: bool
    tor_active: bool
    dns_encrypted: bool
    fingerprint_randomized: bool
    encryption_enabled: bool
    overall_level: PrivacyLevel


@dataclass
class DeepResearchConfig:
    """Configuration for deep research"""
    max_depth: int = 10
    strategy: ExplorationStrategy = ExplorationStrategy.HYBRID
    follow_citations: bool = True
    explore_tangents: bool = True
    max_threads: int = 5
    citation_types: List[str] = field(default_factory=lambda: [
        "academic", "patent", "preprint", "dataset"
    ])


@dataclass
class ExplorationNode:
    """Node in deep research exploration graph"""
    node_id: str
    url: str
    title: str
    depth: int
    parent_id: Optional[str]
    children: List[str] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)
    quality_score: float = 0.0


@dataclass
class GhostAction:
    """GhostDirector action"""
    action_type: ActionType
    parameters: Dict[str, Any]
    priority: int = 5
    requires_stealth: bool = False
    vault_storage: bool = True


@dataclass
class GhostMission:
    """GhostDirector mission"""
    mission_id: str
    goal: str
    actions: List[GhostAction]
    current_step: int = 0
    acquired_loot: List[Dict[str, Any]] = field(default_factory=list)
    anti_loop_counter: int = 0


@dataclass
class DataLeakAlert:
    """Data leak detection alert"""
    alert_id: str
    source: LeakSource
    severity: RiskLevel
    target: str
    leaked_data: Dict[str, Any]
    timestamp: float


@dataclass
class ArchiveSnapshot:
    """Web archive snapshot"""
    url: str
    timestamp: str
    source: ContentSource
    available: bool
    quality_score: float


# =============================================================================
# PRIVACY TYPES
# =============================================================================

class AnonymizationLevel(Enum):
    """PII anonymization levels"""
    NONE = "none"
    PARTIAL = "partial"      # Mask partial data
    FULL = "full"            # Hash replacement
    AGGREGATE = "aggregate"  # Count only


class PrivacyEventCategory(Enum):
    """Privacy audit event categories"""
    DATA_ACCESS = "data_access"
    DATA_MODIFICATION = "data_modification"
    DATA_DELETION = "data_deletion"
    DATA_EXPORT = "data_export"
    CONSENT_GRANTED = "consent_granted"
    CONSENT_REVOKED = "consent_revoked"
    ANONYMIZATION = "anonymization"
    ENCRYPTION = "encryption"


class ProtocolType(Enum):
    """Protocol generation types"""
    MESSAGING = "messaging"
    HANDSHAKE = "handshake"
    ENCRYPTION = "encryption"
    SIGNATURE = "signature"
    ZK_PROOF = "zk_proof"
    MPC = "mpc"


@dataclass
class PrivacyConfig:
    """Privacy layer configuration"""
    level: PrivacyLevel = PrivacyLevel.STANDARD
    
    # Component enables
    enable_privacy_manager: bool = True
    enable_anonymous_comm: bool = True
    enable_audit_log: bool = True
    enable_protocol_gen: bool = False
    
    # VPN settings
    vpn_provider: str = "mullvad"
    vpn_protocol: str = "wireguard"
    
    # Tor settings
    use_tor: bool = False
    tor_use_bridges: bool = False
    
    # DNS settings
    dns_provider: str = "cloudflare"
    dns_protocol: str = "doh"
    
    # Audit settings
    audit_retention_days: int = 90
    audit_encryption: bool = True


# =============================================================================
# TYPE ALIASES (EXTENDED)
# =============================================================================

# Security aliases
ObfuscationPattern = Dict[str, str]
EncryptionKey = Union[str, bytes]
FingerprintConfig = Dict[str, Any]

# Research aliases
CitationGraph = Dict[str, List[str]]
ExplorationTree = Dict[str, ExplorationNode]
GhostLoot = Dict[str, Any]

# Stealth aliases
ProxyConfig = Dict[str, str]
EvasionScript = str
DetectionSignature = Dict[str, Any]

# Privacy aliases
VPNCredentials = Dict[str, str]
PGPKeypair = Dict[str, str]
AuditEntry = Dict[str, Any]

# =============================================================================
# COMMUNICATION TYPES
# =============================================================================

class MessagePriority(Enum):
    """Message priority levels"""
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4
    BACKGROUND = 5


@dataclass
class CommunicationConfig:
    """Communication layer configuration"""
    enable_agent_messaging: bool = True
    enable_model_bridge: bool = True
    enable_emergent_comm: bool = True
    enable_a2a_protocol: bool = True

    # Optimization settings
    enable_batching: bool = True
    enable_compression: bool = True
    batch_timeout_ms: float = 50.0
    max_batch_size: int = 10

    # Routing settings
    semantic_routing: bool = True
    load_balancing: bool = True

    # Protocol settings
    a2a_version: str = "1.0"
    agent_card_ttl: int = 3600


# =============================================================================
# NEUROMORPHIC COMPUTING TYPES
# =============================================================================

class EventType(Enum):
    """Neural event types for neuromorphic computing"""
    SPIKE = "spike"                          # Neuron spiked
    SYNAPTIC_UPDATE = "synaptic_update"      # Synaptic weight update
    LEARNING_UPDATE = "learning_update"      # STDP learning update
    MEMBRANE_UPDATE = "membrane_update"      # Membrane potential update
    NETWORK_RESET = "network_reset"          # Network state reset
    THRESHOLD_CROSS = "threshold_cross"      # Threshold crossing event


class ProcessingState(Enum):
    """Processing states for neuromorphic operations"""
    IDLE = "idle"
    ACTIVE = "active"
    PROCESSING = "processing"
    LEARNING = "learning"
    CONSOLIDATING = "consolidating"
    SLEEPING = "sleeping"


@dataclass(frozen=True)
class SpikeData:
    """Immutable spike event data"""
    neuron_id: int
    timestamp: float
    amplitude: float = 1.0


@dataclass
class NeuralEvent:
    """Neural event for event-driven processing"""
    event_type: EventType
    source_neuron: int
    target_neurons: List[int]
    timestamp: float
    weight_delta: float = 0.0
    priority: int = 5  # 1-10, lower is higher priority
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp == 0:
            object.__setattr__(self, 'timestamp', datetime.now().timestamp())


@dataclass
class ProcessingMetrics:
    """Metrics for neuromorphic processing"""
    energy_consumption_joules: float = 0.0
    spike_count: int = 0
    active_neurons: int = 0
    synaptic_operations: int = 0
    processing_time_ms: float = 0.0
    memory_used_bytes: int = 0


@dataclass
class ProcessingResult:
    """Result from neuromorphic processing"""
    success: bool
    state: ProcessingState
    metrics: ProcessingMetrics
    spike_history: List[SpikeData] = field(default_factory=list)
    output_pattern: Optional[np.ndarray] = None
    error_message: Optional[str] = None


@dataclass
class SNNConfig:
    """Configuration for Spiking Neural Network"""
    n_neurons: int = 1000
    connection_prob: float = 0.1
    use_metal: bool = True
    enable_stdp: bool = True
    v_rest: float = -65.0
    v_thresh: float = -50.0
    tau_m: float = 20.0
    dt: float = 1.0
    refractory_period: float = 2.0


@dataclass
class STDPParams:
    """STDP (Spike-Timing-Dependent Plasticity) parameters"""
    A_plus: float = 0.01       # LTP amplitude
    A_minus: float = -0.0105   # LTD amplitude
    tau_plus: float = 20.0     # LTP time constant (ms)
    tau_minus: float = 20.0    # LTD time constant (ms)
    w_min: float = -1.0        # Minimum weight
    w_max: float = 1.0         # Maximum weight


@dataclass
class NeuronParameters:
    """Biological parameters for LIF neurons"""
    v_rest: float = -65.0      # Resting potential (mV)
    v_reset: float = -65.0     # Reset potential after spike (mV)
    v_thresh: float = -50.0    # Spike threshold (mV)
    tau_m: float = 20.0        # Membrane time constant (ms)
    tau_ref: float = 2.0       # Refractory period (ms)
    resistance: float = 1.0    # Membrane resistance (MΩ)
    noise_std: float = 0.5     # Synaptic noise standard deviation (mV)


@dataclass
class NeuromorphicEnergyReport:
    """Energy efficiency report for neuromorphic computing"""
    total_energy_joules: float
    energy_per_spike_joules: float
    active_neuron_ratio: float
    efficiency_gain_vs_ann: float
    computational_efficiency: float
    co2_emissions_kg: float = 0.0
    trees_equivalent: float = 0.0  # CO2 absorbed by trees per year
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())


@dataclass
class ReservoirConfig:
    """Configuration for Reservoir Computing (ESN/LSM)"""
    reservoir_size: int = 1000
    input_scaling: float = 1.0
    spectral_radius: float = 0.9
    leaking_rate: float = 0.3
    sparsity: float = 0.1
    use_metal: bool = True
    reservoir_type: str = "esn"  # "esn" or "lsm"


@dataclass
class SNNEncryptedContainer:
    """Encrypted container using SNN-based cryptography"""
    ciphertext: bytes
    neural_signature: np.ndarray
    key_id: str
    timestamp: float
    entropy_used: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "ciphertext": base64.b64encode(self.ciphertext).decode(),
            "neural_signature": base64.b64encode(self.neural_signature.tobytes()).decode(),
            "key_id": self.key_id,
            "timestamp": self.timestamp,
            "entropy_used": self.entropy_used
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SNNEncryptedContainer":
        """Create from dictionary"""
        return cls(
            ciphertext=base64.b64decode(data["ciphertext"]),
            neural_signature=np.frombuffer(
                base64.b64decode(data["neural_signature"]),
                dtype=np.float32
            ),
            key_id=data["key_id"],
            timestamp=data["timestamp"],
            entropy_used=data.get("entropy_used", 0)
        )


# =============================================================================
# CANONICAL SCAFFOLD — Activation-Relevant Handoff Surface
# =============================================================================
# This section (lines 1269–1630) is the SOLE canonical surface for
# activation-relevant handoffs between components.
#
# DEFINITION: An activation-relevant handoff is any typed object that:
#   1. Crosses a component boundary (e.g. analyzer→router, windup→export)
#   2. Carries execution state or correlation context
#   3. Would break F9/F11 activation if its shape drifted
#
# KNOWN EXCEPTIONS — sanctioned local seam (see F011_DEEP_RESEARCH_PROVIDER_CONTAINMENT.md)
#   DeepResearchRequest / DeepResearchResponse (enhanced_research.py):
#     Owner: autonomous_orchestrator pipeline (F11 integration)
#     NON-CANONICAL: internal/provider-owned, not public activation surface
#     Removal condition: replaced by canonical ProviderRequest/ProviderResult
#       after F11 migration conditions are met
#     Migration status: F11/F11b complete (triáda, capabilities, tool_registry,
#       grounding hints bridge). DeepResearchRequest/Response remain NON-CANONICAL
#       until F11 provider cutover activates typed seam.
#     Runtime impact if removed: deep_research_provider_seam() becomes untyped wrapper;
#       F11 provider candidate loses typed seam — activation documented in F011 doc
#
# RULES:
#   [1] NEW activation-relevant handoffs MUST be added here, not elsewhere
#   [2] Existing local scaffolds (ghost_executor, execution_coordinator) are
#       marked with non-canonical notes — they are DONOR/COMPAT, not canonical
#   [3] Domain-specific types (FetchResult) live in their domain modules
#   [4] Legacy local scaffolds without explicit NOTE are technical debt
#   [5] New local scaffolds require owner + removal_condition + migration_blocker
#       documented HERE (not only in the donor module)
#
# See: CONTRACT_MATRIX.md for full taxonomy
# =============================================================================

# -----------------------------------------------------------------------------
# CORRELATION SCHEMA — Minimal cross-component identity for tracing
# -----------------------------------------------------------------------------
# Phase 1: Schema/contract only — no mandatory full backfill across codebase.
# Canonical fields for correlating events across ledgers/gates/policies.
# Future phases may extend with additional fields or promote to full dataclass.
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class RunCorrelation:
    """
    Immutable correlation identity for a single research run.

    Fields:
        run_id:     Unique run identifier (used by EvidenceLog, ToolExecLog, MetricsRegistry)
        branch_id:  Research branch/sub-session identifier (for parallel branches)
        provider_id: LLM provider identifier (e.g. "mlx", "openai", "anthropic")
        action_id:  Action/event identifier within the run

    Usage:
        Pass as context to ledger calls for cross-component correlation.
        All fields are optional to allow gradual adoption — do not require all fields.
    """
    run_id: Optional[str] = None
    branch_id: Optional[str] = None
    provider_id: Optional[str] = None
    action_id: Optional[str] = None

    def with_provider(self, provider: str) -> "RunCorrelation":
        """Return new instance with provider_id set."""
        return RunCorrelation(
            run_id=self.run_id,
            branch_id=self.branch_id,
            provider_id=provider,
            action_id=self.action_id,
        )

    def with_action(self, action: str) -> "RunCorrelation":
        """Return new instance with action_id set."""
        return RunCorrelation(
            run_id=self.run_id,
            branch_id=self.branch_id,
            provider_id=self.provider_id,
            action_id=action,
        )

    def to_dict(self) -> Dict[str, Optional[str]]:
        """Serialize to dict for ledger injection."""
        return {
            "run_id": self.run_id,
            "branch_id": self.branch_id,
            "provider_id": self.provider_id,
            "action_id": self.action_id,
        }


# =============================================================================
# PROVIDER CONTRACTS — Sprint 8WA: Canonical LLM Provider Surface
# =============================================================================
# Phase 1 scaffold. Minimal hot-path DTOs for LLM provider calls.
# Future: may extend with streaming, tools, vision modes.
# Removal condition: replaced by fully-typed provider SDK after cutover.
# =============================================================================

@dataclass(slots=True)
class ProviderRequest:
    """
    Canonical input to LLM provider (mlx_lm, openai, anthropic, etc.).

    Fields:
        prompt:           Input prompt string
        model:            Model identifier (e.g. "Hermes-3-Llama-3.2-3B-4bit")
        temperature:      Sampling temperature (0.0-2.0)
        max_tokens:       Maximum tokens to generate
        correlation:      Run correlation context for tracing

    NOTE: This is a PHASE 1 scaffold. Streaming, tools, vision not included.
    Hot-path DTO — keep minimal, no rich context objects.
    """
    prompt: str
    model: str
    temperature: float = 0.3
    max_tokens: int = 512
    correlation: Optional[RunCorrelation] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "correlation": self.correlation.to_dict() if self.correlation else None,
        }


@dataclass(slots=True)
class ProviderResult:
    """
    Canonical output from LLM provider.

    Fields:
        text:         Generated text response
        model:        Model that generated the response
        usage:        Token usage dict (prompt_tokens, completion_tokens, total)
        latency_ms:   Generation latency in milliseconds
        correlation:  Run correlation context (echoed from request)

    Removal condition: replaced by fully-typed provider SDK response.
    """
    text: str
    model: str
    usage: Dict[str, int]
    latency_ms: float
    correlation: Optional[RunCorrelation] = None

    @property
    def prompt_tokens(self) -> int:
        return self.usage.get("prompt_tokens", 0)

    @property
    def completion_tokens(self) -> int:
        return self.usage.get("completion_tokens", 0)

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_tokens", 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
            "correlation": self.correlation.to_dict() if self.correlation else None,
        }


# =============================================================================
# ACTION CONTRACTS — Sprint 8WA: Canonical Action Execution Surface
# =============================================================================
# Phase 1 scaffold. ActionRequest/ActionResult for tool/action execution.
# Future: may add ActionContext, ActionStatus, retry fields.
# Removal condition: replaced by typed ActionProtocol after cutover.
# =============================================================================

@dataclass(slots=True)
class ExecutionRequest:
    """
    Canonical request to execute an action/tool.

    Fields:
        action_type:   Action identifier (e.g. "web_search", "stealth_crawler")
        parameters:    Action-specific parameters dict
        priority:      Execution priority 1-10 (lower = higher priority)
        correlation:   Run correlation for cross-component tracing

    Future canonical consumer: ActionOrchestrator in runtime/sprint_scheduler.py
    Removal condition: replaced by typed ActionProtocol.
    """
    action_type: str
    parameters: Dict[str, Any]
    priority: int = 5
    correlation: Optional[RunCorrelation] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type,
            "parameters": self.parameters,
            "priority": self.priority,
            "correlation": self.correlation.to_dict() if self.correlation else None,
        }


@dataclass(slots=True)
class ExecutionResult:
    """
    Canonical result from action execution.

    Fields:
        action_type:     Echo of requested action
        success:         Whether action succeeded
        data:            Action-specific result data
        execution_time:  Execution duration in seconds
        error:           Error message if failed
        correlation:     Echoed from request

    NOTE: Existing ActionResult (Ghost) at line ~531 is a DIFFERENT contract.
    GhostActionResult lives in the Ghost layer. This is the generic action result.
    They MAY be unified in a future phase but NOT during phase 1 scaffold.

    Removal condition: replaced by typed ActionProtocol.
    """
    action_type: str
    success: bool
    data: Dict[str, Any]
    execution_time: float
    error: Optional[str] = None
    correlation: Optional[RunCorrelation] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type,
            "success": self.success,
            "data": self.data,
            "execution_time": self.execution_time,
            "error": self.error,
            "correlation": self.correlation.to_dict() if self.correlation else None,
        }


# =============================================================================
# BRANCH DECISION — Sprint 8WA: Canonical Branch Routing Surface
# =============================================================================
# Phase 1 scaffold. Decision about research flow branching.
# Future: may add BranchContext, alternative branches, rollback support.
# Removal condition: replaced by typed BranchProtocol.
# =============================================================================

@dataclass(slots=True)
class BranchDecision:
    """
    Canonical decision about research branch routing.

    Fields:
        decision_id:    Unique decision identifier
        branch_id:      Target branch identifier (chosen branch)
        alternatives:   List of considered branch IDs
        reasoning:      LLM reasoning for the decision
        confidence:     Decision confidence 0.0-1.0
        correlation:    Run correlation context

    Future canonical consumer: SprintScheduler branch routing logic.
    Removal condition: replaced by typed BranchProtocol.
    """
    decision_id: str
    branch_id: str
    alternatives: List[str]
    reasoning: str
    confidence: float
    correlation: Optional[RunCorrelation] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "branch_id": self.branch_id,
            "alternatives": self.alternatives,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "correlation": self.correlation.to_dict() if self.correlation else None,
        }


# =============================================================================
# SCHEDULER-SHADOW / HANDOFF TYPED NOTES — Sprint F0.45
# =============================================================================
# Typed fact bundles for scheduler-shadow and handoff world.
# These are COMPATIBILITY scaffolds — canonical types already defined above.
#
# LifecycleSnapshot: FUTURE canonical owner = runtime/sprint_lifecycle.py
#   Replacement: WorkflowPhase + ControlPhase + WindupLocalPhase bundles
#   Removal condition: promoted to canonical in sprint_lifecycle.py
#
# ProviderRecommendation: FUTURE canonical owner = capabilities.py
#   Replacement: typed recommendation with readiness tiers
#   Removal condition: capabilities.py provides typed provider_recommend
#
# NOTE: This section does NOT introduce new DTOs. It documents the typed
# fact contracts that already exist in runtime/shadow_inputs.py and
# capabilities.py. The goal is to prevent new competing DTO worlds from
# forming outside types.py canonical scaffold.
# =============================================================================

# Naming convention note:
# - Sprint spec used ActionRequest/ActionResult naming.
# - Canonical types use ExecutionRequest/ExecutionResult naming.
# - Ghost layer has its own ActionResult (line ~531) — DIFFERENT contract.
# - DO NOT create ActionRequest/ActionResult aliases — would conflict with Ghost ActionResult.
# - Call-sites should use ExecutionRequest/ExecutionResult directly.


# =============================================================================
# EXPORT HANDOFF — Sprint 8WA: Windup → Export Phase Contract
# =============================================================================
# Phase 1 scaffold. Data passed from windup_engine to sprint_exporter.
# Wraps existing scorecard dict + scheduler reference.
# Future: may add structured findings, graph snapshot, hypothesis list.
# Removal condition: replaced by typed WindupResult after cutover.
# =============================================================================

@dataclass(slots=True)
class ExportHandoff:
    """
    Canonical handoff from windup phase to export phase.

    Fields:
        sprint_id:                Sprint identifier
        scorecard:                Scorecard dict (existing windup output)
        ranked_parquet:           Path to ranked parquet file (or None)
        synthesis_engine:         Synthesis engine used
        gnn_predictions:          GNN prediction count
        top_nodes:                Top IOC graph nodes
        phase_durations:           Phase timing dict
        correlation:              Run correlation context
        runtime_truth:            Canonical runtime-truth record (additive)
        execution_context:        Empirical run boundary record (additive)
        canonical_run_summary:    CHECKPOINT-0 enriched operator summary (additive)
        synthesis_outcome_payload: Serialized SynthesisOutcome seam (additive)

    NOTE: This is a COMPAT handoff — wraps existing dict-based scorecard.
    The scorecard dict is the current canonical form; this scaffold provides
    a typed wrapper that will become the canonical form post-cutover.

    Future canonical consumer: sprint_exporter.export_sprint()
    Removal condition: scorecard replaced by structured WindupResult.
    """
    sprint_id: str
    scorecard: Dict[str, Any]
    ranked_parquet: Optional[str] = None
    synthesis_engine: str = "unknown"
    gnn_predictions: int = 0
    top_nodes: List[Any] = field(default_factory=list)
    phase_durations: Dict[str, float] = field(default_factory=dict)
    correlation: Optional[RunCorrelation] = None
    # Sprint F155: Canonical truth enrichment — additive payload from same run
    runtime_truth: Dict[str, Any] = field(default_factory=dict)
    execution_context: Dict[str, Any] = field(default_factory=dict)
    canonical_run_summary: Dict[str, Any] = field(default_factory=dict)
    synthesis_outcome_payload: Optional[Dict[str, Any]] = None
    # Sprint F153: Top-level sprint verdict — posture, confidence, next action
    sprint_verdict: Optional[Dict[str, Any]] = None

    @classmethod
    def from_windup(
        cls,
        sprint_id: str,
        scorecard: Dict[str, Any],
        correlation: Optional[RunCorrelation] = None,
    ) -> "ExportHandoff":
        """
        Create ExportHandoff from windup phase output (scorecard dict).

        COMPAT-ONLY post-Sprint 8VZ: This classmethod is retained for legacy
        call-sites that pass raw scorecard dicts. The canonical producer path
        in __main__ now constructs ExportHandoff(...) directly, sourcing
        top_nodes from store.get_top_seed_nodes().

        CURRENT COMPAT SEAM (post-8VZ):
        Used only by non-main call-sites that still pass scorecard dicts.
        Canonical __main__ path uses ExportHandoff(...) constructor directly.

        REMOVAL CONDITION (shortened post-8VZ):
        __main__ now uses direct constructor; this classmethod remains only
        for backward-compat callers. Full removal when all callers are gone.

        Args:
            sprint_id: sprint identifier
            scorecard: dict from windup phase (contains windup facts)
            correlation: optional run correlation context

        Returns:
            ExportHandoff — typed handoff with fields extracted from scorecard dict
        """
        return cls(
            sprint_id=sprint_id,
            scorecard=scorecard,
            ranked_parquet=scorecard.get("ranked_parquet"),
            synthesis_engine=scorecard.get("synthesis_engine_used", "unknown"),
            gnn_predictions=scorecard.get("gnn_predicted_links", 0),
            top_nodes=scorecard.get("top_graph_nodes", []),
            phase_durations=scorecard.get("phase_duration_seconds", {}),
            correlation=correlation,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sprint_id": self.sprint_id,
            "scorecard": self.scorecard,
            "ranked_parquet": self.ranked_parquet,
            "synthesis_engine": self.synthesis_engine,
            "gnn_predictions": self.gnn_predictions,
            "top_nodes": self.top_nodes,
            "phase_durations": self.phase_durations,
            "correlation": self.correlation.to_dict() if self.correlation else None,
            # Sprint F155: Canonical truth enrichment — typed seam now serializes
            "runtime_truth": self.runtime_truth,
            "execution_context": self.execution_context,
            "canonical_run_summary": self.canonical_run_summary,
            "synthesis_outcome_payload": self.synthesis_outcome_payload,
            # Sprint F153: Top-level sprint verdict
            "sprint_verdict": self.sprint_verdict,
        }


# =============================================================================
# CANONICAL GROUNDING HINTS — Sprint F11b: Minimal Grounding Bridge
# =============================================================================
# Minimal canonical surface for deep research grounding metadata.
# Future canonical target for DeepResearchGroundingShim (enhanced_research.py).
# This is NOT a replacement for ProviderRequest/ProviderResult.
# This is NOT a new correlation mechanism — reuses RunCorrelation.
#
# KNOWN NON-CANONICAL SEAMS (sanctioned local seams):
#   DeepResearchGroundingShim (enhanced_research.py:2636):
#     Owner: enhanced_research local seam
#     Non-canonical because: internal/provider-owned, not public surface
#     Removal condition: replaced by CanonicalGroundingHints after F11 activation
#   grounding_hints Dict (enhanced_research.py:2555):
#     Owner: DeepResearchRequest local seam
#     Non-canonical because: raw Dict, not typed
#     Removal condition: replaced by typed CanonicalGroundingHints after migration
#
# RULES:
#   [1] This is a FUTURE canonical target, not immediate migration
#   [2] Local seam remains non-canonical until activation conditions are met
#   [3] from_shim() is COMPAT ONLY — not for use in hot path
# =============================================================================

@dataclass(frozen=True)
class CanonicalGroundingHints:
    """
    Canonical minimal grounding hints for deep research handoff.

    This is the FUTURE canonical target for the local seam in enhanced_research.py.
    The local seam (DeepResearchGroundingShim, grounding_hints dict) remains
    non-canonical until migration conditions are met per F011 activation plan.

    Fields:
        topic_hints:    Topic keywords for retrieval alignment (immutable tuple)
        domain_tags:    Domain classification tags (immutable tuple)
        correlation:    Run correlation context (reuses RunCorrelation)
        budget_hint:    Optional budget tier hint (read-only string)
        evidence_hint:  Optional evidence requirement hint (read-only string)

    Shrink wrap: Keep minimal. Only add fields with explicit migration trigger.
    """
    topic_hints: Tuple[str, ...] = field(default_factory=lambda: ())
    domain_tags: Tuple[str, ...] = field(default_factory=lambda: ())
    correlation: Optional[RunCorrelation] = None
    budget_hint: Optional[str] = None
    evidence_hint: Optional[str] = None

    @classmethod
    def from_shim(cls, shim: Any = None, topic_hints: Tuple[str, ...] = (), domain_tags: Tuple[str, ...] = (), correlation: Optional[RunCorrelation] = None) -> "CanonicalGroundingHints":
        """
        Create from local seam Shim for forward-compatibility.

        COMPAT ONLY: This method bridges the non-canonical local seam
        to the canonical surface. Do NOT call this in hot path.

        Supports two calling conventions:
        - from_shim(shim) — extract from DeepResearchGroundingShim
        - from_shim(topic_hints=..., domain_tags=..., correlation=...) — direct construction
        """
        if shim is not None:
            # Extract from Shim
            # NOTE: _BudgetHints has stagnation_tolerance/confidence_boost, NOT budget_tier
            # NOTE: _EvidenceHints has log_level/detail_depth, NOT evidence_required
            # budget_hint and evidence_hint are forward-compat placeholders for future migration
            budget_hint = None
            evidence_hint = None
            if hasattr(shim, 'budget_hints') and shim.budget_hints is not None:
                # Map budget_hints to forward-compat budget_hint string
                bh = shim.budget_hints
                if hasattr(bh, 'stagnation_tolerance') and bh.stagnation_tolerance > 0:
                    budget_hint = f"stagnation_tolerance:{bh.stagnation_tolerance}"
                elif hasattr(bh, 'confidence_boost') and bh.confidence_boost != 0.0:
                    budget_hint = f"confidence_boost:{bh.confidence_boost}"
            if hasattr(shim, 'evidence_hints') and shim.evidence_hints is not None:
                # Map evidence_hints to forward-compat evidence_hint string
                eh = shim.evidence_hints
                if hasattr(eh, 'detail_depth'):
                    evidence_hint = eh.detail_depth
                elif hasattr(eh, 'log_level'):
                    evidence_hint = eh.log_level
            return cls(
                topic_hints=tuple(getattr(shim, 'topic_hints', [])),
                domain_tags=tuple(getattr(shim, 'domain_tags', [])),
                correlation=getattr(shim, 'correlation', None),
                budget_hint=budget_hint,
                evidence_hint=evidence_hint,
            )
        return cls(
            topic_hints=topic_hints,
            domain_tags=domain_tags,
            correlation=correlation,
        )

    def is_empty(self) -> bool:
        """Returns True if no grounding hints are set."""
        return (
            len(self.topic_hints) == 0
            and len(self.domain_tags) == 0
            and self.correlation is None
            and self.budget_hint is None
            and self.evidence_hint is None
        )


# =============================================================================
# FUTURE: WINDUP HANDOFF & WARMUP HANDOFF (Phase 2+)
# =============================================================================
# Placeholder scaffolds for future phases. Not implemented in Phase 1.
#
# WindupHandoff: Active → Windup phase contract (scorecard source)
#   - Future canonical consumer: windup_engine.run_windup()
#   - Removal condition: replaced by typed WindupPhaseResult
#
# WarmupHandoff: Init → Active phase contract (sprint config source)
#   - Future canonical consumer: warmup logic in SprintScheduler
#   - Removal condition: replaced by typed WarmupResult
#
# These will be added in Sprint 8WB (Windup phase) and Sprint 8WC (Warmup phase).
# =============================================================================
