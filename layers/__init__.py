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
from .coordination_layer import CoordinationLayer, GhostWatchdog, DriverStatus
from .ghost_layer import GhostLayer, SystemContext, VMThreatLevel, ProcessType
from .memory_layer import (
    MemoryLayer,
    RAMDiskManager,
    RAMDiskConfig,
    SharedMemoryManager,
    EntropyMaskingManager,
    SharedMemoryBlock,
)
from .privacy_layer import PrivacyLayer
from .research_layer import ResearchLayer
from .security_layer import SecurityLayer, MissionAudit, AuditEntry
from .stealth_layer import (
    StealthLayer,
    BehaviorSimulator,
    SimulationConfig,
    BehaviorPattern,
    MouseMovement,
    ScrollAction,
    Chameleon,
    # Fingerprint Randomizer (from stealth_toolkit integration)
    FingerprintRandomizer,
    FingerprintConfig,
    BrowserProfile,
)
from .content_layer import (
    ContentCleaner,
    SimpleHTMLCleaner,
    ResiliparseCleaner,
    CleaningResult,
    OutputFormat,
    get_content_cleaner,
    # Utility functions (from stealth_crawler integration)
    clean_html_tags,
    extract_url_from_duckduckgo_redirect,
    extract_url_from_google_redirect,
    clean_search_result_url,
    SearchResultItem,
    parse_duckduckgo_results,
    parse_google_results,
)
from .hive_coordination import (
    ConnectedCoordinationSystem,
    CoordinationLayer as HiveCoordinationLayer,
    CoordinationNode,
    CoordinationTask,
    TopologyType,
)
from .smart_coordination import (
    SmartSpawnedCoordinationIntegration,
    SmartSpawnedAgent,
    SmartSpawnedRole,
)
from .layer_manager import (
    LayerManager,
    LayerStatus,
    LayerHealth,
    create_layer_manager,
    get_layer_manager,
    # NEW: Unified Capabilities Manager
    UnifiedCapabilitiesManager,
    create_capabilities_manager,
    get_capabilities_manager,
)
from .temporal_signal_layer import (
    TemporalEvent,
    TemporalScore,
    TemporalEdgeCandidate,
    _KeyState,
    TemporalSignalLayer,
    event_from_finding_like,
)
from .temporal_signal_store import TemporalSignalStore
from .temporal_signal_runtime import (
    get_temporal_signal_layer,
    reset_temporal_signal_layer,
    get_temporal_signal_summary,
    is_temporal_store_enabled,
    get_temporal_signal_store,
    load_temporal_signal_snapshot,
    save_temporal_signal_snapshot,
    close_temporal_signal_store,
    build_temporal_priority_hints,
)

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
    "CoordinationLayer",
    "GhostWatchdog",
    "DriverStatus",
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


def get_stealth_layer() -> "StealthLayer | None":
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


def get_content_layer() -> "ContentCleaner | None":
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
