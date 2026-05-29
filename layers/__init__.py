"""
Universal Orchestrator Layers
=============================

Modular layers for the universal orchestrator:
- GhostLayer: GhostDirector integration with anti-loop protection
- MemoryLayer: M1 memory management and context swap
- CoordinationLayer: Coordinator delegation and decision management
- SecurityLayer: Cryptography, obfuscation, secure destruction
- StealthLayer: Stealth browsing, detection evasion, CAPTCHA solving
- ResearchLayer: GhostDirector, deep research, depth maximization
- PrivacyLayer: VPN/Tor, PGP, audit logging, protocol generation
- CommunicationLayer: Agent messaging, model bridge, A2A protocol
- ContentLayer: HTML cleaning, Markdown conversion, MLX-optimized
- LayerManager: Centralized layer orchestration and lifecycle management
"""

from .communication_layer import CommunicationLayer
from .content_layer import (
    CleaningResult,
    ContentCleaner,
    OutputFormat,
    ResiliparseCleaner,
    SearchResultItem,
    SimpleHTMLCleaner,
    # Utility functions (from stealth_crawler integration)
    clean_html_tags,
    clean_search_result_url,
    extract_url_from_duckduckgo_redirect,
    extract_url_from_google_redirect,
    get_content_cleaner,
    parse_duckduckgo_results,
    parse_google_results,
)
from .ghost_layer import GhostLayer, ProcessType, SystemContext, VMThreatLevel
from .hive_coordination import (
    ConnectedCoordinationSystem,
    CoordinationNode,
    CoordinationTask,
    TopologyType,
)
from .hive_coordination import (
    CoordinationLayer as HiveCoordinationLayer,
)
from .layer_manager import (
    LayerHealth,
    LayerManager,
    LayerStatus,
    # NEW: Unified Capabilities Manager
    UnifiedCapabilitiesManager,
    create_capabilities_manager,
    create_layer_manager,
    get_capabilities_manager,
    get_layer_manager,
)
from .memory_layer import (
    EntropyMaskingManager,
    MemoryLayer,
    RAMDiskConfig,
    RAMDiskManager,
    SharedMemoryBlock,
    SharedMemoryManager,
)
from .privacy_layer import PrivacyLayer
from .research_layer import ResearchLayer
from .security_layer import AuditEntry, MissionAudit, SecurityLayer
from .smart_coordination import (
    SmartSpawnedAgent,
    SmartSpawnedCoordinationIntegration,
    SmartSpawnedRole,
)
from .stealth_layer import (
    BehaviorPattern,
    BehaviorSimulator,
    BrowserProfile,
    Chameleon,
    FingerprintConfig,
    # Fingerprint Randomizer (from stealth_toolkit integration)
    FingerprintRandomizer,
    MouseMovement,
    ScrollAction,
    SimulationConfig,
    StealthLayer,
)
from .temporal_signal_layer import (
    TemporalEdgeCandidate,
    TemporalEvent,
    TemporalScore,
    TemporalSignalLayer,
    _KeyState,
    event_from_finding_like,
)
from .temporal_signal_runtime import (
    build_temporal_priority_hints,
    close_temporal_signal_store,
    get_temporal_signal_layer,
    get_temporal_signal_store,
    get_temporal_signal_summary,
    is_temporal_store_enabled,
    load_temporal_signal_snapshot,
    reset_temporal_signal_layer,
    save_temporal_signal_snapshot,
)
from .temporal_signal_store import TemporalSignalStore

__all__ = [
    "GhostLayer",
    "SystemContext",
    "VMThreatLevel",
    "ProcessType",
    "MemoryLayer",
    "RAMDiskManager",
    "RAMDiskConfig",
    "SharedMemoryManager",
    "EntropyMaskingManager",
    "SharedMemoryBlock",
    "SecurityLayer",
    "MissionAudit",
    "AuditEntry",
    "StealthLayer",
    "BehaviorSimulator",
    "SimulationConfig",
    "BehaviorPattern",
    "MouseMovement",
    "ScrollAction",
    "Chameleon",
    # Fingerprint Randomizer
    "FingerprintRandomizer",
    "FingerprintConfig",
    "BrowserProfile",
    "ResearchLayer",
    "PrivacyLayer",
    "CommunicationLayer",
    # Content
    "ContentCleaner",
    "SimpleHTMLCleaner",
    "ResiliparseCleaner",
    "CleaningResult",
    "OutputFormat",
    "get_content_cleaner",
    # Content utilities (from stealth_crawler)
    "clean_html_tags",
    "extract_url_from_duckduckgo_redirect",
    "extract_url_from_google_redirect",
    "clean_search_result_url",
    "SearchResultItem",
    "parse_duckduckgo_results",
    "parse_google_results",
    # Hive Coordination
    "ConnectedCoordinationSystem",
    "HiveCoordinationLayer",
    "CoordinationNode",
    "CoordinationTask",
    "TopologyType",
    # Smart Coordination
    "SmartSpawnedCoordinationIntegration",
    "SmartSpawnedAgent",
    "SmartSpawnedRole",
    # Layer Management
    "LayerManager",
    "LayerStatus",
    "LayerHealth",
    "create_layer_manager",
    "get_layer_manager",
    # Unified Capabilities
    "UnifiedCapabilitiesManager",
    "create_capabilities_manager",
    "get_capabilities_manager",
    # Temporal Signal Runtime (Sprint F206P/F206Q)
    "get_temporal_signal_layer",
    "reset_temporal_signal_layer",
    "get_temporal_signal_summary",
    "is_temporal_store_enabled",
    "get_temporal_signal_store",
    "load_temporal_signal_snapshot",
    "save_temporal_signal_snapshot",
    "close_temporal_signal_store",
    "build_temporal_priority_hints",
    # Temporal Signal Layer & Store classes
    "TemporalSignalStore",
    "TemporalSignalLayer",
    "TemporalEvent",
    "TemporalScore",
    "TemporalEdgeCandidate",
    "event_from_finding_like",
]

# ---------------------------------------------------------------------------
# Layer factory getters — lazy singletons for fetch pipeline injection
# ---------------------------------------------------------------------------


def get_stealth_layer() -> StealthLayer | None:
    """Lazy singleton StealthLayer accessor.

    Returns None if layers are disabled or init fails (fail-soft).
    Caller is responsible for calling .initialize() if returning a new instance.
    """
    try:
        from hledac.universal.layers.stealth_layer import StealthLayer
    except Exception:
        return None
    try:
        instance = StealthLayer()
        return instance
    except Exception:
        return None


def get_content_layer() -> ContentCleaner | None:
    """Lazy singleton ContentCleaner accessor.

    Returns None if content_layer init fails (fail-soft).
    ContentCleaner.clean_html() is sync — safe to call from async fetch pipeline.
    """
    try:
        from hledac.universal.layers.content_layer import ContentCleaner
    except Exception:
        return None
    try:
        return ContentCleaner()
    except Exception:
        return None
