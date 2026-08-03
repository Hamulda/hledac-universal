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
import os
import msgspec
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Self
if TYPE_CHECKING:
    import numpy as np
    from .autonomous_analyzer import AutoResearchProfile

class ResearchMode(Enum):
    """Research depth modes"""
    QUICK = 'quick'
    STANDARD = 'standard'
    DEEP = 'deep'
    EXTREME = 'extreme'
    AUTONOMOUS = 'autonomous'

class ActionResultType(Enum):
    """Strict typed handler result taxonomy for truthful benchmark."""
    SUCCESS = 'SUCCESS'
    EMPTY = 'EMPTY'
    NETWORK_UNAVAILABLE = 'NETWORK_UNAVAILABLE'
    UPSTREAM_API_ERROR = 'UPSTREAM_API_ERROR'
    TIMEOUT = 'TIMEOUT'
    EXCEPTION = 'EXCEPTION'
    MOCK_FALLBACK_USED = 'MOCK_FALLBACK_USED'

class OfflineModeError(Exception):
    """Raised when network operations are attempted in offline mode."""
    pass

def is_offline_mode() -> bool:
    """Check if offline mode is enabled via HLEDAC_OFFLINE environment variable."""
    return os.getenv('HLEDAC_OFFLINE', '0') == '1'

class OrchestratorState(Enum):
    """Main orchestrator state machine states"""
    IDLE = 'idle'
    PLANNING = 'planning'
    BRAIN = 'brain'
    EXECUTION = 'execution'
    SYNTHESIS = 'synthesis'
    ERROR = 'error'

class SystemState(Enum):
    """System health state machine (from InfrastructureOrchestrator)"""
    HEALTHY = 'healthy'
    MEMORY_PRESSURE = 'memory_pressure'
    THERMAL_THROTTLING = 'thermal_throttling'
    DEGRADED = 'degraded'
    RECOVERY = 'recovery'

class AgentState(Enum):
    """Sub-agent states"""
    IDLE = 'idle'
    PLANNING = 'planning'
    EXECUTING = 'executing'
    COMPLETED = 'completed'
    FAILED = 'failed'
    LOST = 'lost'

class SubAgentType(Enum):
    """Types of sub-agents"""
    STEALTH_WEB = 'stealth_web'
    OSINT = 'osint'
    SECURITY = 'security'
    ARCHIVE = 'archive'
    ACADEMIC = 'academic'
    SYNTHESIS = 'synthesis'

class Severity(Enum):
    """Severity levels for logging and alerts"""
    DEBUG = 'debug'
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'

class SecurityLevel(Enum):
    """Security levels for privacy protection"""
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    CRITICAL = 'critical'

class ActionType(Enum):
    """GhostDirector action types (18+ actions)"""
    SCAN = 'scan'
    GOOGLE = 'google'
    DOWNLOAD = 'download'
    SEARCH = 'search'
    SMART_SEARCH = 'smart_search'
    MEMORIZE = 'memorize'
    PROBE = 'probe'
    TRACK = 'track'
    RESEARCH_PAPER = 'research_paper'
    DEEP_RESEARCH = 'deep_research'
    DEEP_READ = 'deep_read'
    ANSWER = 'answer'
    CRACK = 'crack'
    ERROR = 'error'
    ARCHIVE_FALLBACK = 'archive_fallback'
    FACT_CHECK = 'fact_check'
    STEALTH_HARVEST = 'stealth_harvest'
    OSINT_DISCOVERY = 'osint_discovery'
    EXTRACT_ENTITIES = 'extract_entities'
    ANALYZE_SENTIMENT = 'analyze_sentiment'
    SUMMARIZE = 'summarize'

class OperationType(Enum):
    """Operation types for coordinator delegation"""
    RESEARCH = 'research'
    SECURITY = 'security'
    EXECUTION = 'execution'
    MONITORING = 'monitoring'
    ANALYSIS = 'analysis'
    SYNTHESIS = 'synthesis'

class ResearchPhase(Enum):
    """Research execution phases"""
    INITIALIZATION = 'initialization'
    EXPLORATION = 'exploration'
    DEEP_DIVE = 'deep_dive'
    ANALYSIS = 'analysis'
    SYNTHESIS = 'synthesis'
    FINALIZATION = 'finalization'

class QueryComplexity(Enum):
    """Query complexity levels (from MODOrchestrator)"""
    SIMPLE = 'simple'
    MODERATE = 'moderate'
    COMPLEX = 'complex'
    VERY_COMPLEX = 'very_complex'

class ReasoningMode(Enum):
    """Reasoning modes for autonomous orchestration"""
    STANDARD = 'standard'
    CHAIN_OF_THOUGHT = 'chain_of_thought'
    TREE_OF_THOUGHTS = 'tree_of_thoughts'
    HYBRID_TOT_MOE = 'hybrid_tot_moe'

class ModelConfig(msgspec.Struct, gc=False):
    """Model configuration for M1 8GB - 3 model stack only"""
    HERMES_MODEL: str = 'mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit'
    HERMES_CONTEXT: int = 8192
    HERMES_TEMP: float = 0.3
    MODERNBERT_MODEL: str = 'mlx-community/answerdotai-ModernBERT-base-6bit'
    EMBED_DIM: int = 768
    GLINER_MODEL: str = 'knowledgator/gliner-relex-large-v0.5'

class ResearchConfig(msgspec.Struct, gc=False):
    """Research execution configuration"""
    mode: ResearchMode = ResearchMode.STANDARD
    max_steps: int = 20
    max_time_minutes: int = 30
    memory_limit_mb: float = 5500.0
    hermes_model: str = 'mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit'
    modernbert_model: str = 'mlx-community/answerdotai-ModernBERT-base-6bit'
    gliner_model: str = 'knowledgator/gliner-relex-large-v0.5'
    enable_knowledge_graph: bool = False
    enable_rag: bool = True
    db_path: str | None = None
    enable_stealth: bool = True
    auto_stealth: bool = True
    privacy_level: str = 'high'
    chaff_ratio: float = 0.3
    enable_audit: bool = True
    enable_autonomy: bool = True
    auto_archive_fallback: bool = True
    enable_fact_checking: bool = True
    output_format: str = 'markdown'
    save_intermediate: bool = True
    use_ram_vault: bool = True
    vault_password: str | None = None
    max_concurrent_agents: int = 3
    agent_timeout: int = 300

class MemoryConfig(msgspec.Struct, gc=False):
    """Memory management configuration (from InfrastructureOrchestrator)"""
    memory_limit_mb: float = 5500.0
    max_rss_gb: float = 5.5
    thermal_threshold_c: float = 85.0
    enable_secure_enclave: bool = True
    enable_metal_acceleration: bool = True
    recovery_interval_seconds: float = 30.0
    health_check_interval_seconds: float = 5.0

class GhostConfig(msgspec.Struct, gc=False):
    """Ghost layer configuration"""
    max_steps: int = 20
    enable_vault: bool = True
    vault_size_mb: int = 256
    enable_anti_loop: bool = True
    stagnation_threshold: int = 3
    enable_loot_manager: bool = True

class SecurityConfig(msgspec.Struct, gc=False):
    """Security configuration for privacy protection"""
    enable_audit: bool = True
    privacy_level: str = 'high'
    use_ram_vault: bool = True
    vault_password: str | None = None
    pii_detection: bool = True
    auto_redact: bool = True
    obfuscation_level: str = 'medium'
    generate_decoys: bool = True
    decoy_count: int = 20
    wipe_standard: str = 'nist_800_88'
    verification_enabled: bool = True
    rename_before_delete: bool = True
    enable_query_masking: bool = True
    enable_chaff_traffic: bool = True
    chaff_ratio: float = 0.3
    enable_timing_jitter: bool = True
    jitter_percent: float = 50.0

class StealthConfig(msgspec.Struct, gc=False):
    """Stealth mode configuration"""
    enabled: bool = True
    chaff_ratio: float = 0.3
    rotate_identity: bool = True
    use_tor: bool = False
    use_proxy: bool = False
    proxy_url: str | None = None
    timing_jitter: bool = True
    user_agent_rotation: bool = True
    browser_type: str = 'chromium'
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
    captcha_providers: list[str] = msgspec.field(default_factory=lambda: ['2captcha', 'anticaptcha'])
    captcha_timeout: int = 120
    enable_proxy_rotation: bool = False
    proxy_list: list[str] = msgspec.field(default_factory=list)
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
    enable_image_ocr: bool = False
    ocr_model: str = 'microsoft/trocr-base-handwritten'
    max_image_size: int = 2048
    confidence_threshold: float = 0.5
    randomize_timezone: bool = True
    randomize_webgl: bool = True
    randomize_fonts: bool = True
    randomize_plugins: bool = True
    consistent_per_session: bool = True
    session_duration: int = 300
    platform: str = 'macos'
    pattern: str = 'default'
    min_delay: float = 0.1
    max_delay: float = 0.5
    randomness: float = 0.3
    mouse_speed: float = 1.0
    scroll_min: int = 20
    scroll_max: int = 50
    scroll_pause: float = 0.2

class CoordinationConfig(msgspec.Struct, gc=False):
    """Coordination layer configuration"""
    max_context_length: int = 1024
    temperature: float = 0.1
    max_tokens_response: int = 100
    enable_delegation: bool = True

class AgentManagerConfig(msgspec.Struct, gc=False):
    """Agent management configuration (from EnhancedUnifiedOrchestrator)"""
    max_concurrent_agents: int = 6
    memory_threshold_mb: float = 512.0
    agent_timeout_seconds: float = 25.0
    circuit_breaker_threshold: int = 3
    agent_pool_size: int = 2
    auto_optimize_interval: int = 300

class ExecutionContext(msgspec.Struct, gc=False):
    """Context for research execution (from v1 + v2)"""
    query: str
    current_step: int = 0
    max_steps: int = 20
    state: OrchestratorState = OrchestratorState.IDLE
    execution_history: list[dict[str, Any]] = msgspec.field(default_factory=list)
    action_log: list[dict[str, Any]] = msgspec.field(default_factory=list)
    collected_data: list[dict[str, Any]] = msgspec.field(default_factory=list)
    knowledge_graph: dict[str, Any] = msgspec.field(default_factory=dict)
    stealth_activated: bool = False
    blocked_domains: set[str] = msgspec.field(default_factory=set)
    visited_urls: set[str] = msgspec.field(default_factory=set)
    content_hashes: set[str] = msgspec.field(default_factory=set)
    start_time: float = msgspec.field(default_factory=lambda: datetime.now(UTC).timestamp())
    tokens_used: int = 0

    def add_action(self, action_type: ActionType, details: dict[str, Any]) -> None:
        """Add action to log"""
        self.action_log.append({'step': self.current_step, 'action': action_type.value, 'timestamp': datetime.now(UTC).isoformat(), 'details': details})

class DecisionContext(msgspec.Struct, gc=False):
    """Context for decision making (from Hermes3)"""
    research_id: str
    goal: str
    phase: ResearchPhase
    iterations: int = 0
    max_iterations: int = 20
    context_data: dict[str, Any] = msgspec.field(default_factory=dict)

class SubAgentResult(msgspec.Struct, gc=False):
    """Result from sub-agent execution"""
    agent_type: SubAgentType
    success: bool
    data: dict[str, Any]
    confidence: float
    sources: list[dict[str, Any]]
    execution_time: float
    state: AgentState

class ResearchResult(msgspec.Struct, gc=False):
    """Final research result"""
    success: bool
    query: str
    mode: ResearchMode
    final_answer: str
    sources: list[dict[str, Any]] = msgspec.field(default_factory=list)
    knowledge_graph: dict[str, Any] = msgspec.field(default_factory=dict)
    execution_history: list[dict[str, Any]] = msgspec.field(default_factory=list)
    agent_results: list[SubAgentResult] = msgspec.field(default_factory=list)
    statistics: dict[str, Any] = msgspec.field(default_factory=dict)
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)

    def to_markdown(self) -> str:
        """Export result as Markdown"""
        lines = [f'# Research Report: {self.query}', '', f'**Mode:** {self.mode.value}', f"**Success:** {('✅' if self.success else '❌')}", f'**Sources:** {len(self.sources)}', f'**Agents Used:** {len([r for r in self.agent_results if r.success])}', '', '## Answer', '', self.final_answer, '', '## Sources', '']
        for i, source in enumerate(self.sources, 1):
            lines.append(f"{i}. [{source.get('title', 'Unknown')}]({source.get('url', '#')})")
        if self.statistics:
            lines.extend(['', '## Statistics', '', '```json', f'{self._dict_to_json(self.statistics)}', '```'])
        return '\n'.join(lines)

    @staticmethod
    def _dict_to_json(d: dict) -> str:
        """Simple dict to JSON string"""
        import json
        return json.dumps(d, indent=2, default=str)

class DecisionRequest(msgspec.Struct, gc=False):
    """Request for decision making (from DeepSeek R1)"""
    operation_type: OperationType
    context: dict[str, Any]
    priority: int = 5
    timeout_seconds: float = 30.0
    requires_delegation: bool = True

class DecisionResponse(msgspec.Struct, gc=False):
    """Response from decision making"""
    decision_id: str
    operation_type: OperationType
    action: str
    parameters: dict[str, Any]
    confidence: float
    coordinator_id: str | None = None
    reasoning: str | None = None

class ActionResult(msgspec.Struct, gc=False):
    """Result from Ghost action execution"""
    action: ActionType
    success: bool
    data: dict[str, Any]
    execution_time: float
    stagnation_detected: bool = False
    stored_in_vault: bool = False

class SystemMetrics(msgspec.Struct, gc=False):
    """System health metrics (from InfrastructureOrchestrator)"""
    memory_used_mb: float
    memory_available_mb: float
    cpu_percent: float
    temperature_c: float | None
    state: SystemState
    timestamp: float

class AgentMetrics(msgspec.Struct, gc=False):
    """Agent performance metrics"""
    agent_type: SubAgentType
    success_rate: float
    avg_execution_time: float
    circuit_breaker_open: bool
    consecutive_failures: int
    total_executions: int

class ComplexityAnalysis(msgspec.Struct, gc=False):
    """Complexity analysis result for ToT decision making"""
    score: float
    requires_multi_step: bool
    estimated_depth: int
    tot_recommended: bool
    indicators: dict[str, float]

class AnalyzerResult(msgspec.Struct, gc=False):
    """
    Structured output from AutonomousAnalyzer.

    Canonical form for the analyzer -> capability router -> tool registry pipeline.
    Wraps AutoResearchProfile for typed capability routing.

    NOTE: This is a bridge type. The underlying AutoResearchProfile remains
    the source of truth for analyzer output until full migration.
    """
    tools: set[str] = msgspec.field(default_factory=set)
    sources: set[str] = msgspec.field(default_factory=set)
    privacy_level: str = 'STANDARD'
    use_tor: bool = False
    models_needed: set[str] = msgspec.field(default_factory=set)
    depth: str = 'STANDARD'
    max_time: float = 300.0
    use_tot: bool = False
    tot_mode: str = 'standard'
    reasoning: str = ''
    _raw_profile: Any | None = None

    @classmethod
    def from_profile(cls, profile: AutoResearchProfile) -> AnalyzerResult:
        """
        Create AnalyzerResult from AutoResearchProfile.

        This is an adapter bridge - the AutoResearchProfile is preserved
        in _raw_profile for backward compatibility.
        """
        return cls(tools=profile.tools.copy(), sources=profile.sources.copy(), privacy_level=profile.privacy_level, use_tor=profile.use_tor, models_needed=profile.models_needed.copy(), depth=profile.depth, max_time=profile.max_time, use_tot=profile.use_tot, tot_mode=profile.tot_mode, reasoning=profile.reasoning, _raw_profile=profile)

    def to_capability_signal(self) -> dict[str, Any]:
        """
        Convert to capability signal for CapabilityRouter.

        Returns a typed dict that CapabilityRouter.route() can process.
        """
        return {'tools': self.tools, 'sources': self.sources, 'privacy_level': self.privacy_level, 'use_tor': self.use_tor, 'depth': self.depth, 'use_tot': self.use_tot, 'tot_mode': self.tot_mode, 'requires_embeddings': bool(self.models_needed & {'modernbert'}), 'requires_ner': bool(self.models_needed & {'gliner'}), 'requires_temporal': 'temporal_analyzer' in self.tools, 'requires_crypto': 'blockchain_analyzer' in self.tools}

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

class UniversalResearchOrchestrator:
    """
    Base class for universal research orchestrators.

    Provides common interface and base functionality for all orchestrators
    in the Hledac universal system.

    This is an abstract base class - concrete implementations should
    override the research method.
    """
    __slots__ = tuple(('_initialized', 'config', 'state'))

    def __init__(self, config: ResearchConfig | None=None):
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

    async def research(self, query: str, search_func: Any | None=None, domain: str='general') -> Any:
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
        raise NotImplementedError('Subclasses must implement research()')

    async def cleanup(self) -> None:
        """Cleanup resources."""
        self._initialized = False
        self.state = OrchestratorState.IDLE

    def get_stats(self) -> dict[str, Any]:
        """Get orchestrator statistics."""
        return {'state': self.state.value, 'initialized': self._initialized}

class ObfuscationLevel(Enum):
    """String/content obfuscation levels"""
    NONE = 'none'
    LIGHT = 'light'
    MEDIUM = 'medium'
    HEAVY = 'heavy'
    MAXIMUM = 'maximum'

class WipeStandard(Enum):
    """Secure data destruction standards"""
    NIST_800_88 = 'nist_800_88'
    DoD_5220_22M = 'dod_5220_22m'
    GUTMANN = 'gutmann'

class RiskLevel(Enum):
    """Detection risk levels (CANONICAL — lowercase str values).

    Single source of truth for OSINT risk classification. All other
    RiskLevel definitions across the codebase MUST be aliases or
    imports of this enum. Comparison of str vs int/float values
    is a silent bug — see `assert` below.
    """
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    CRITICAL = 'critical'
assert RiskLevel.HIGH.value == 'high', 'RiskLevel values must be lowercase strings; check sibling definitions'

class BrowserType(Enum):
    """Browser types for stealth"""
    CHROMIUM = 'chromium'
    FIREFOX = 'firefox'
    WEBKIT = 'webkit'

class CaptchaType(Enum):
    """CAPTCHA types"""
    RECAPTCHA_V2 = 'recaptcha_v2'
    RECAPTCHA_V3 = 'recaptcha_v3'
    HCAPTCHA = 'hcaptcha'
    FUNCAPTCHA = 'funcaptcha'
    IMAGE = 'image'
    GEETEST = 'geetest'

class PrivacyLevel(Enum):
    """Privacy protection levels"""
    NONE = 'none'
    BASIC = 'basic'
    STANDARD = 'standard'
    ENHANCED = 'enhanced'
    MAXIMUM = 'maximum'

class ExplorationStrategy(Enum):
    """Deep research exploration strategies"""
    DEPTH_FIRST = 'depth_first'
    BREADTH_FIRST = 'breadth_first'
    CITATION_FOLLOWING = 'citation'
    TANGENT_EXPLORATION = 'tangent'
    HYBRID = 'hybrid'

class CommunicationPattern(Enum):
    """Protocol communication patterns"""
    REQUEST_RESPONSE = 'request_response'
    STREAMING = 'streaming'
    PUB_SUB = 'pub_sub'

class LeakSource(Enum):
    """Data leak sources"""
    BREACH_DATABASE = 'breach_database'
    DARK_WEB = 'dark_web'
    PASTE_SITE = 'paste_site'
    SOCIAL_MEDIA = 'social_media'
    PUBLIC_RECORDS = 'public_records'

class ContentSource(Enum):
    """Archive content sources"""
    WAYBACK = 'wayback'
    SEARCH_CACHE = 'search_cache'
    SOCIAL_ARCHIVE = 'social_archive'

class ObfuscationResult(msgspec.Struct, gc=False):
    """Result of string obfuscation"""
    original_hash: str
    obfuscated_data: str
    encoding_chain: list[str]
    decoy_count: int
    success: bool

class DestructionResult(msgspec.Struct, gc=False):
    """Result of secure data destruction"""
    file_path: str
    standard: WipeStandard
    passes_completed: int
    bytes_overwritten: int
    verification_passed: bool
    timestamp: float

class StealthSession(msgspec.Struct, gc=False):
    """Stealth browsing session"""
    session_id: str
    browser_type: BrowserType
    fingerprint: dict[str, Any]
    proxy: str | None
    risk_level: RiskLevel
    created_at: float

class CaptchaSolution(msgspec.Struct, gc=False):
    """CAPTCHA solving result"""
    solution: str
    solved_at: float
    cost: float
    confidence: float
    provider: str

class PrivacyStatus(msgspec.Struct, gc=False):
    """Current privacy/anonymity status"""
    vpn_connected: bool
    tor_active: bool
    dns_encrypted: bool
    fingerprint_randomized: bool
    encryption_enabled: bool
    overall_level: PrivacyLevel

class DeepResearchConfig(msgspec.Struct, gc=False):
    """Configuration for deep research"""
    max_depth: int = 10
    strategy: ExplorationStrategy = ExplorationStrategy.HYBRID
    follow_citations: bool = True
    explore_tangents: bool = True
    max_threads: int = 5
    citation_types: list[str] = msgspec.field(default_factory=lambda: ['academic', 'patent', 'preprint', 'dataset'])

class ExplorationNode(msgspec.Struct, gc=False):
    """Node in deep research exploration graph"""
    node_id: str
    url: str
    title: str
    depth: int
    parent_id: str | None
    children: list[str] = msgspec.field(default_factory=list)
    citations: list[str] = msgspec.field(default_factory=list)
    quality_score: float = 0.0

class GhostAction(msgspec.Struct, gc=False):
    """GhostDirector action"""
    action_type: ActionType
    parameters: dict[str, Any]
    priority: int = 5
    requires_stealth: bool = False
    vault_storage: bool = True

class GhostMission(msgspec.Struct, gc=False):
    """GhostDirector mission"""
    mission_id: str
    goal: str
    actions: list[GhostAction]
    current_step: int = 0
    acquired_loot: list[dict[str, Any]] = msgspec.field(default_factory=list)
    anti_loop_counter: int = 0

class DataLeakAlert(msgspec.Struct, gc=False):
    """Data leak detection alert"""
    alert_id: str
    source: LeakSource
    severity: RiskLevel
    target: str
    leaked_data: dict[str, Any]
    timestamp: float

class ArchiveSnapshot(msgspec.Struct, gc=False):
    """Web archive snapshot"""
    url: str
    timestamp: str
    source: ContentSource
    available: bool
    quality_score: float

class AnonymizationLevel(Enum):
    """PII anonymization levels"""
    NONE = 'none'
    PARTIAL = 'partial'
    FULL = 'full'
    AGGREGATE = 'aggregate'

class PrivacyEventCategory(Enum):
    """Privacy audit event categories"""
    DATA_ACCESS = 'data_access'
    DATA_MODIFICATION = 'data_modification'
    DATA_DELETION = 'data_deletion'
    DATA_EXPORT = 'data_export'
    CONSENT_GRANTED = 'consent_granted'
    CONSENT_REVOKED = 'consent_revoked'
    ANONYMIZATION = 'anonymization'
    ENCRYPTION = 'encryption'

class ProtocolType(Enum):
    """Protocol generation types"""
    MESSAGING = 'messaging'
    HANDSHAKE = 'handshake'
    ENCRYPTION = 'encryption'
    SIGNATURE = 'signature'
    ZK_PROOF = 'zk_proof'
    MPC = 'mpc'

class PrivacyConfig(msgspec.Struct, gc=False):
    """Privacy layer configuration"""
    level: PrivacyLevel = PrivacyLevel.STANDARD
    enable_privacy_manager: bool = True
    enable_anonymous_comm: bool = True
    enable_audit_log: bool = True
    enable_protocol_gen: bool = False
    vpn_provider: str = 'mullvad'
    vpn_protocol: str = 'wireguard'
    use_tor: bool = False
    tor_use_bridges: bool = False
    dns_provider: str = 'cloudflare'
    dns_protocol: str = 'doh'
    audit_retention_days: int = 90
    audit_encryption: bool = True
ObfuscationPattern = dict[str, str]
EncryptionKey = str | bytes
FingerprintConfig = dict[str, Any]
CitationGraph = dict[str, list[str]]
ExplorationTree = dict[str, ExplorationNode]
GhostLoot = dict[str, Any]
ProxyConfig = dict[str, str]
EvasionScript = 'EvasionScript'  # structured — see layers.evasion_pipeline.EvasionScript
DetectionSignature = dict[str, Any]
VPNCredentials = dict[str, str]
PGPKeypair = dict[str, str]
AuditEntry = dict[str, Any]

class MessagePriority(Enum):
    """Message priority levels"""
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4
    BACKGROUND = 5

class CommunicationConfig(msgspec.Struct, gc=False):
    """Communication layer configuration"""
    enable_agent_messaging: bool = True
    enable_model_bridge: bool = True
    enable_emergent_comm: bool = True
    enable_a2a_protocol: bool = True
    enable_batching: bool = True
    enable_compression: bool = True
    batch_timeout_ms: float = 50.0
    max_batch_size: int = 10
    semantic_routing: bool = True
    load_balancing: bool = True
    a2a_version: str = '1.0'
    agent_card_ttl: int = 3600

class EventType(Enum):
    """Neural event types for neuromorphic computing"""
    SPIKE = 'spike'
    SYNAPTIC_UPDATE = 'synaptic_update'
    LEARNING_UPDATE = 'learning_update'
    MEMBRANE_UPDATE = 'membrane_update'
    NETWORK_RESET = 'network_reset'
    THRESHOLD_CROSS = 'threshold_cross'

class ProcessingState(Enum):
    """Processing states for neuromorphic operations"""
    IDLE = 'idle'
    ACTIVE = 'active'
    PROCESSING = 'processing'
    LEARNING = 'learning'
    CONSOLIDATING = 'consolidating'
    SLEEPING = 'sleeping'

class SpikeData(msgspec.Struct, frozen=True, gc=False):
    """Immutable spike event data"""
    neuron_id: int
    timestamp: float
    amplitude: float = 1.0

@dataclass(slots=True)
class NeuralEvent:
    """Neural event for event-driven processing"""
    event_type: EventType
    source_neuron: int
    target_neurons: list[int]
    timestamp: float
    weight_delta: float = 0.0
    priority: int = 5
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp == 0:
            object.__setattr__(self, 'timestamp', datetime.now(UTC).timestamp())

class ProcessingMetrics(msgspec.Struct, gc=False):
    """Metrics for neuromorphic processing"""
    energy_consumption_joules: float = 0.0
    spike_count: int = 0
    active_neurons: int = 0
    synaptic_operations: int = 0
    processing_time_ms: float = 0.0
    memory_used_bytes: int = 0

class ProcessingResult(msgspec.Struct, gc=False):
    """Result from neuromorphic processing"""
    success: bool
    state: ProcessingState
    metrics: ProcessingMetrics
    spike_history: list[SpikeData] = msgspec.field(default_factory=list)
    output_pattern: np.ndarray | None = None
    error_message: str | None = None

class SNNConfig(msgspec.Struct, gc=False):
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

class STDPParams(msgspec.Struct, gc=False):
    """STDP (Spike-Timing-Dependent Plasticity) parameters"""
    A_plus: float = 0.01
    A_minus: float = -0.0105
    tau_plus: float = 20.0
    tau_minus: float = 20.0
    w_min: float = -1.0
    w_max: float = 1.0

class NeuronParameters(msgspec.Struct, gc=False):
    """Biological parameters for LIF neurons"""
    v_rest: float = -65.0
    v_reset: float = -65.0
    v_thresh: float = -50.0
    tau_m: float = 20.0
    tau_ref: float = 2.0
    resistance: float = 1.0
    noise_std: float = 0.5

class NeuromorphicEnergyReport(msgspec.Struct, gc=False):
    """Energy efficiency report for neuromorphic computing"""
    total_energy_joules: float
    energy_per_spike_joules: float
    active_neuron_ratio: float
    efficiency_gain_vs_ann: float
    computational_efficiency: float
    co2_emissions_kg: float = 0.0
    trees_equivalent: float = 0.0
    timestamp: float = msgspec.field(default_factory=lambda: datetime.now(UTC).timestamp())

class ReservoirConfig(msgspec.Struct, gc=False):
    """Configuration for Reservoir Computing (ESN/LSM)"""
    reservoir_size: int = 1000
    input_scaling: float = 1.0
    spectral_radius: float = 0.9
    leaking_rate: float = 0.3
    sparsity: float = 0.1
    use_metal: bool = True
    reservoir_type: str = 'esn'

class SNNEncryptedContainer(msgspec.Struct, gc=False):
    """Encrypted container using SNN-based cryptography"""
    ciphertext: bytes
    neural_signature: np.ndarray
    key_id: str
    timestamp: float
    entropy_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization"""
        import base64
        return {'ciphertext': base64.b64encode(self.ciphertext).decode(), 'neural_signature': base64.b64encode(self.neural_signature.tobytes()).decode(), 'key_id': self.key_id, 'timestamp': self.timestamp, 'entropy_used': self.entropy_used}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SNNEncryptedContainer:
        """Create from dictionary"""
        import base64
        return cls(ciphertext=base64.b64decode(data['ciphertext']), neural_signature=np.frombuffer(base64.b64decode(data['neural_signature']), dtype=np.float32), key_id=data['key_id'], timestamp=data['timestamp'], entropy_used=data.get('entropy_used', 0))

class RunCorrelation(msgspec.Struct, frozen=True, gc=False):
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
    run_id: str | None = None
    branch_id: str | None = None
    provider_id: str | None = None
    action_id: str | None = None

    def with_provider(self, provider: str) -> RunCorrelation:
        """Return new instance with provider_id set."""
        return RunCorrelation(run_id=self.run_id, branch_id=self.branch_id, provider_id=provider, action_id=self.action_id)

    def with_action(self, action: str) -> RunCorrelation:
        """Return new instance with action_id set."""
        return RunCorrelation(run_id=self.run_id, branch_id=self.branch_id, provider_id=self.provider_id, action_id=action)

    def to_dict(self) -> dict[str, str | None]:
        """Serialize to dict for ledger injection."""
        return {'run_id': self.run_id, 'branch_id': self.branch_id, 'provider_id': self.provider_id, 'action_id': self.action_id}

class ProviderRequest(msgspec.Struct, gc=False):
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
    correlation: RunCorrelation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {'prompt': self.prompt, 'model': self.model, 'temperature': self.temperature, 'max_tokens': self.max_tokens, 'correlation': self.correlation.to_dict() if self.correlation else None}

class ProviderResult(msgspec.Struct, gc=False):
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
    usage: dict[str, int]
    latency_ms: float
    correlation: RunCorrelation | None = None

    @property
    def prompt_tokens(self) -> int:
        return self.usage.get('prompt_tokens', 0)

    @property
    def completion_tokens(self) -> int:
        return self.usage.get('completion_tokens', 0)

    @property
    def total_tokens(self) -> int:
        return self.usage.get('total_tokens', 0)

    def to_dict(self) -> dict[str, Any]:
        return {'text': self.text, 'model': self.model, 'usage': self.usage, 'latency_ms': self.latency_ms, 'correlation': self.correlation.to_dict() if self.correlation else None}

class ExecutionRequest(msgspec.Struct, gc=False):
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
    parameters: dict[str, Any]
    priority: int = 5
    correlation: RunCorrelation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {'action_type': self.action_type, 'parameters': self.parameters, 'priority': self.priority, 'correlation': self.correlation.to_dict() if self.correlation else None}

class ExecutionResult(msgspec.Struct, gc=False):
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
    data: dict[str, Any]
    execution_time: float
    error: str | None = None
    correlation: RunCorrelation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {'action_type': self.action_type, 'success': self.success, 'data': self.data, 'execution_time': self.execution_time, 'error': self.error, 'correlation': self.correlation.to_dict() if self.correlation else None}

class BranchDecision(msgspec.Struct, gc=False):
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
    alternatives: list[str]
    reasoning: str
    confidence: float
    correlation: RunCorrelation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {'decision_id': self.decision_id, 'branch_id': self.branch_id, 'alternatives': self.alternatives, 'reasoning': self.reasoning, 'confidence': self.confidence, 'correlation': self.correlation.to_dict() if self.correlation else None}

class ExportHandoff(msgspec.Struct, gc=False):
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
    scorecard: dict[str, Any]
    ranked_parquet: str | None = None
    synthesis_engine: str = 'unknown'
    gnn_predictions: int = 0
    top_nodes: list[Any] = msgspec.field(default_factory=list)
    phase_durations: dict[str, float] = msgspec.field(default_factory=dict)
    correlation: RunCorrelation | None = None
    runtime_truth: dict[str, Any] = msgspec.field(default_factory=dict)
    execution_context: dict[str, Any] = msgspec.field(default_factory=dict)
    canonical_run_summary: dict[str, Any] = msgspec.field(default_factory=dict)
    synthesis_outcome_payload: dict[str, Any] | None = None
    sprint_verdict: dict[str, Any] | None = None
    analyst_brief: dict[str, Any] | None = None
    timer_events: list[dict[str, Any]] | None = None
    # APEX-1009: Uncertainty flags from synthesis — propagated to export for annotation
    uncertainty_flags: dict[str, Any] | None = None

    @classmethod
    def from_windup(cls, sprint_id: str, scorecard: dict[str, Any], correlation: RunCorrelation | None=None) -> ExportHandoff:
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
        return cls(sprint_id=sprint_id, scorecard=scorecard, ranked_parquet=scorecard.get('ranked_parquet'), synthesis_engine=scorecard.get('synthesis_engine_used', 'unknown'), gnn_predictions=scorecard.get('gnn_predicted_links', 0), top_nodes=scorecard.get('top_graph_nodes', []), phase_durations=scorecard.get('phase_duration_seconds', {}), correlation=correlation)

    def to_dict(self) -> dict[str, Any]:
        return {'sprint_id': self.sprint_id, 'scorecard': self.scorecard, 'ranked_parquet': self.ranked_parquet, 'synthesis_engine': self.synthesis_engine, 'gnn_predictions': self.gnn_predictions, 'top_nodes': self.top_nodes, 'phase_durations': self.phase_durations, 'correlation': self.correlation.to_dict() if self.correlation else None, 'runtime_truth': self.runtime_truth, 'execution_context': self.execution_context, 'canonical_run_summary': self.canonical_run_summary, 'synthesis_outcome_payload': self.synthesis_outcome_payload, 'sprint_verdict': self.sprint_verdict, 'uncertainty_flags': self.uncertainty_flags}

    def __repr__(self) -> str:
        """Stable debug repr — shows key fields without eval risk."""
        rn = len(self.top_nodes) if self.top_nodes else 0
        sc_keys = len(self.scorecard) if self.scorecard else 0
        rt = 'yes' if self.runtime_truth else 'no'
        return f'ExportHandoff(sprint_id={self.sprint_id!r}, top_nodes={rn}, scorecard_keys={sc_keys}, runtime_truth={rt})'

class CanonicalGroundingHints(msgspec.Struct, frozen=True, gc=False):
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
    topic_hints: tuple[str, ...] = msgspec.field(default_factory=lambda: ())
    domain_tags: tuple[str, ...] = msgspec.field(default_factory=lambda: ())
    correlation: RunCorrelation | None = None
    budget_hint: str | None = None
    evidence_hint: str | None = None

    @classmethod
    def from_shim(cls, shim: Any=None, topic_hints: tuple[str, ...]=(), domain_tags: tuple[str, ...]=(), correlation: RunCorrelation | None=None) -> CanonicalGroundingHints:
        """
        Create from local seam Shim for forward-compatibility.

        COMPAT ONLY: This method bridges the non-canonical local seam
        to the canonical surface. Do NOT call this in hot path.

        Supports two calling conventions:
        - from_shim(shim) — extract from DeepResearchGroundingShim
        - from_shim(topic_hints=..., domain_tags=..., correlation=...) — direct construction
        """
        if shim is not None:
            budget_hint = None
            evidence_hint = None
            if hasattr(shim, 'budget_hints') and shim.budget_hints is not None:
                bh = shim.budget_hints
                if hasattr(bh, 'stagnation_tolerance') and bh.stagnation_tolerance > 0:
                    budget_hint = f'stagnation_tolerance:{bh.stagnation_tolerance}'
                elif hasattr(bh, 'confidence_boost') and bh.confidence_boost != 0.0:
                    budget_hint = f'confidence_boost:{bh.confidence_boost}'
            if hasattr(shim, 'evidence_hints') and shim.evidence_hints is not None:
                eh = shim.evidence_hints
                if hasattr(eh, 'detail_depth'):
                    evidence_hint = eh.detail_depth
                elif hasattr(eh, 'log_level'):
                    evidence_hint = eh.log_level
            return cls(topic_hints=tuple(getattr(shim, 'topic_hints', [])), domain_tags=tuple(getattr(shim, 'domain_tags', [])), correlation=getattr(shim, 'correlation', None), budget_hint=budget_hint, evidence_hint=evidence_hint)
        return cls(topic_hints=topic_hints, domain_tags=domain_tags, correlation=correlation)

    def is_empty(self) -> bool:
        """Returns True if no grounding hints are set."""
        return len(self.topic_hints) == 0 and len(self.domain_tags) == 0 and (self.correlation is None) and (self.budget_hint is None) and (self.evidence_hint is None)


# ─────────────────────────────────────────────────────────────────────────────
# UNIFIED-004: Micro-Sprint Types for Entropy Feedback Loop
# ─────────────────────────────────────────────────────────────────────────────

class MicroSprintPlan(msgspec.Struct, frozen=True, gc=False):
    """
    Lightweight targeted re-fetch plan for high-entropy entities.

    UNIFIED-004: Closes the entropy feedback loop by providing a structured
    plan for re-fetching a single entity with alternative protocols when
    entropy quality is below threshold.

    Design constraints (M1 8GB):
    - max_hops: 1-2 (bounded graph traversal)
    - timeout: 30s max (prevents sprint starvation)
    - protocols: alternative discovery protocols (e.g., ["ct", "passive_dns"])

    Fields:
        entity_id: Target entity identifier (URL, domain, IP, hash)
        entropy: Measured entropy score (0.0-1.0) that triggered re-fetch
        protocols: Alternative discovery protocols to try (ordered by priority)
        max_hops: Maximum graph traversal depth (1-2, default 2)
        timeout: Hard timeout in seconds (default 30.0, max 30.0)
        reason: Human-readable reason for re-fetch (optional)
    """
    entity_id: str
    entropy: float
    protocols: tuple[str, ...] = msgspec.field(default_factory=tuple)
    max_hops: int = 2
    timeout: float = 30.0
    reason: str | None = None

    @classmethod
    def create(
        cls,
        entity_id: str,
        entropy: float,
        protocols: list[str] | tuple[str, ...] = (),
        max_hops: int = 2,
        timeout: float = 30.0,
        reason: str | None = None,
    ) -> MicroSprintPlan:
        """Factory with constraint validation."""
        if not (0.0 <= entropy <= 1.0):
            raise ValueError(f"entropy must be in [0.0, 1.0], got {entropy}")
        if not (1 <= max_hops <= 2):
            raise ValueError(f"max_hops must be 1 or 2, got {max_hops}")
        if not (0.0 < timeout <= 30.0):
            raise ValueError(f"timeout must be in (0.0, 30.0], got {timeout}")
        return cls(
            entity_id=entity_id,
            entropy=entropy,
            protocols=tuple(protocols),
            max_hops=max_hops,
            timeout=timeout,
            reason=reason,
        )


class MicroSprintResult(msgspec.Struct, frozen=True, gc=False):
    """
    Result of a micro-sprint execution.

    UNIFIED-004: Captures outcome of targeted re-fetch attempt.

    Fields:
        entity_id: Target entity that was re-fetched
        success: Whether re-fetch produced improved entropy
        new_entropy: New entropy score after re-fetch (0.0 if failed)
        protocols_tried: Protocols actually attempted (subset of plan.protocols)
        evidence_ids: Evidence IDs created during micro-sprint
        duration_ms: Execution time in milliseconds
        error: Error message if failed (optional)
        hops_explored: Number of graph hops actually explored
    """
    entity_id: str
    success: bool
    new_entropy: float = 0.0
    protocols_tried: tuple[str, ...] = msgspec.field(default_factory=lambda: ())
    evidence_ids: tuple[str, ...] = msgspec.field(default_factory=lambda: ())
    duration_ms: float = 0.0
    error: str | None = None
    hops_explored: int = 0

    def entropy_improvement(self) -> float:
        """Calculate entropy improvement (new - old). Requires old entropy context."""
        return self.new_entropy

    def is_meaningful_improvement(self, threshold: float = 0.1) -> bool:
        """Check if improvement exceeds meaningful threshold."""
        return self.success and self.new_entropy > threshold