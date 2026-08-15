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

import functools
from typing import TypeVar
from collections.abc import Callable

from .communication_layer import CommunicationLayer

# ── Generic Layer Cached Factory ──────────────────────────────────────────────
# Refactored from 4x identical patterns (lines 241-299) — deduplicated 2026-08-07
_T = TypeVar("_T")


def _make_cached_layer_getter(
    layer_name: str,
    import_path: str,
    factory_call: str,
    singleton_args: tuple[()] = (),
) -> Callable[[], _T | None]:
    """
    Factory: create a @lru_cache'd layer getter with fail-soft import.

    DRY pattern replacing 4x identical _*_layer_cached() functions.

    Args:
        layer_name: Human-readable name for logging (e.g. "StealthLayer")
        import_path: Dot-path to import (e.g. "hledac.universal.layers.stealth_layer")
        factory_call: Constructor expression (e.g. "StealthLayer()")
        singleton_args: Tuple of args passed to the constructor

    Returns:
        Cached getter function returning Layer instance or None on failure.
    """

    @functools.lru_cache(maxsize=1)
    def _cached_getter() -> _T | None:
        try:
            # Dynamic import of the layer class
            module_path, class_name = import_path.rsplit(".", 1)
            module = __import__(module_path, fromlist=[class_name])
            layer_cls = getattr(module, class_name)
            return layer_cls(*singleton_args)
        except Exception:
            return None

    return _cached_getter


# ── Pre-built cached getters (DRY — replaced 4x copy-paste patterns) ──────────

_stealth_layer_getter = _make_cached_layer_getter(
    layer_name="StealthLayer",
    import_path="hledac.universal.layers.stealth_layer.StealthLayer",
    factory_call="StealthLayer()",
)

_content_layer_getter = _make_cached_layer_getter(
    layer_name="ContentCleaner",
    import_path="hledac.universal.layers.content_layer.ContentCleaner",
    factory_call="ContentCleaner()",
)

_ghost_layer_getter = _make_cached_layer_getter(
    layer_name="GhostLayer",
    import_path="hledac.universal.layers.ghost_layer.GhostLayer",
    factory_call="GhostLayer(config=None)",
    singleton_args=(None,),
)
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
# Unified evasion pipeline (APEX-1005/1006/1007)
from .evasion_pipeline import (
    EvasionCategory,
    EvasionScript,
    FingerprintProfile,
    ProfileGenerator,
    _EvasionScriptGenerator,
    compute_detection_score,
    generate_evasion_scripts,
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





    demo_connected_coordination,
    demo_smart_spawned_integration,
    run_all_demos,
)

__all__ = [
    "GhostLayer",
    "SystemContext",
    "VMThreatLevel",

from _core import aclose    "ProcessType",
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
    # Unified Evasion Pipeline (APEX-1005/1006/1007)
    "EvasionCategory",
    "EvasionScript",
    "FingerprintProfile",
    "ProfileGenerator",
    "generate_evasion_scripts",
    "compute_detection_score",
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
    # Examples & Demos (moved from coordination modules)
    "demo_connected_coordination",
    "demo_smart_spawned_integration",
    "run_all_demos",
]

# Layer factory getters — lazy singletons for fetch pipeline injection


def get_stealth_layer() -> StealthLayer | None:
    """Lazy singleton StealthLayer accessor — module-level cached, init-once reuse."""
    return _stealth_layer_getter()


def get_content_layer() -> ContentCleaner | None:
    """Lazy singleton ContentCleaner accessor — module-level cached, init-once reuse."""
    return _content_layer_getter()


def get_ghost_layer() -> GhostLayer | None:
    """Lazy singleton GhostLayer accessor — module-level cached, init-once reuse."""
    return _ghost_layer_getter()


# CommunicationLayer has non-default constructor args — keep inline for clarity
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
