"""
Universal Orchestrator Layers

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

Issue 6.1: Layer Protocol + LayerStack for IoC cross-cutting concerns.
"""
from __future__ import annotations

import functools

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
    UnifiedCapabilitiesManager,
    create_capabilities_manager,
    create_layer_manager,
    get_capabilities_manager,
    get_layer_manager,
)
from .layer_protocol import (
    Layer,
    LayerContext,
    LayerEvent,
    LayerStack,
    # UNIX Domain Socket (zero-copy IPC)
    create_uds_server,
    uds_fetch,
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
    _KeyState,  # noqa: F401, E402  # .temporal_signal_layer._KeyState
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
from .ua_rotator import (
    UARotator,
    build_randomized_headers,
    get_random_ua,
    get_ua_for_profile,
    get_random_accept_language,
    get_random_accept_encoding,
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
    # Layer Protocol (Issue 6.1)
    "Layer",
    "LayerContext",
    "LayerEvent",
    "LayerStack",
    "create_uds_server",
    "uds_fetch",
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
    # UA Rotator (Issue 10.2)
    "UARotator",
    "get_random_ua",
    "get_ua_for_profile",
    "get_random_accept_language",
    "get_random_accept_encoding",
    "build_randomized_headers",
]

# Layer factory getters — lazy singletons for fetch pipeline injection


@functools.lru_cache(maxsize=1)
def _stealth_layer_cached() -> StealthLayer | None:
    """Cached StealthLayer instance — called only once, reused forever."""
    try:
        from hledac.universal.layers.stealth_layer import StealthLayer
        return StealthLayer()
    except Exception:
        return None


def get_stealth_layer() -> StealthLayer | None:
    """Lazy singleton StealthLayer accessor — module-level cached, init-once reuse."""
    return _stealth_layer_cached()


@functools.lru_cache(maxsize=1)
def _content_layer_cached() -> ContentCleaner | None:
    """Cached ContentCleaner instance — called only once, reused forever."""
    try:
        from hledac.universal.layers.content_layer import ContentCleaner
        return ContentCleaner()
    except Exception:
        return None


def get_content_layer() -> ContentCleaner | None:
    """Lazy singleton ContentCleaner accessor — module-level cached, init-once reuse."""
    return _content_layer_cached()


@functools.lru_cache(maxsize=1)
def _communication_layer_cached() -> CommunicationLayer | None:
    """Cached CommunicationLayer instance — called only once, reused forever."""
    try:
        from hledac.universal.layers.communication_layer import CommunicationLayer as _CL  # noqa: N814
        from hledac.universal.project_types import CommunicationConfig
        return _CL(config=CommunicationConfig())
    except Exception:
        return None


def get_communication_layer() -> CommunicationLayer | None:
    """Lazy singleton CommunicationLayer accessor — module-level cached, init-once reuse."""
    return _communication_layer_cached()


@functools.lru_cache(maxsize=1)
def _ghost_layer_cached() -> GhostLayer | None:
    """Cached GhostLayer instance — called only once, reused forever."""
    try:
        from hledac.universal.layers.ghost_layer import GhostLayer as _GL  # noqa: N814
        return _GL(config=None)
    except Exception:
        return None


def get_ghost_layer() -> GhostLayer | None:
    """Lazy singleton GhostLayer accessor — module-level cached, init-once reuse."""
    return _ghost_layer_cached()
