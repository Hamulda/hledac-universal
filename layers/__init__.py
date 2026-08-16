"""
Universal Orchestrator Layers
=============================

ISSUE-006: Consolidated architecture with 5 coherent layer modules.

Legacy modules (deprecated, kept for backward compatibility):
- ghost_layer.py, security_layer.py, privacy_layer.py, etc.

New consolidated modules:
- layers/core/ - Protocol, BaseLayer, LayerRegistry
- layers/ghost.py - Ghost orchestrator + SystemContext
- layers/security.py - Security + Privacy
- layers/research.py - Research + TemporalSignal
- layers/communication.py - Communication + Content
- layers/stealth.py - Stealth + Evasion + Behavior

Usage:
    # New architecture
    from layers.core import LayerRegistry, BaseLayer, LayerContext
    from layers.ghost import GhostLayer
    from layers.security import SecurityLayer

    registry = LayerRegistry()
    registry.register('ghost', GhostLayer())

    # Legacy compatibility
    from layers import GhostLayer, SecurityLayer, StealthLayer  # Still works
"""
from __future__ import annotations

import functools
from typing import TypeVar, Callable

# ─── New Consolidated Modules ───────────────────────────────────────────────────

# Core architecture
from layers.core import (
    BaseLayer,
    Layer,
    LayerContext,
    LayerEvent,
    LayerRegistry,
    LayerStack,
)

# Consolidated layers
from layers.ghost import GhostLayer
from layers.security import SecurityLayer, MissionAudit, AuditEntry
from layers.research import ResearchLayer, TemporalSignalLayer, TemporalEvent, TemporalScore
from layers.communication import CommunicationLayer, ContentCleaner, OutputFormat, CleaningResult
from layers.stealth import (
    StealthLayer,
    BehaviorSimulator,
    BehaviorPattern,
    ProfileGenerator,
    FingerprintProfile,
)

# ─── Legacy Re-exports (Backward Compatibility) ────────────────────────────────
import warnings
import logging

_logger = logging.getLogger(__name__)

def _create_deprecated_alias(new_module: str, old_class: str, new_class: str):
    """Create a deprecated alias with warning."""
    def _deprecated_alias(*args, **kwargs):
        warnings.warn(
            f"layers.{old_class} is deprecated. Import from {new_module}.{new_class} instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        from importlib import import_module
        module = import_module(new_module)
        cls = getattr(module, new_class)
        return cls(*args, **kwargs)
    return _deprecated_alias

# Legacy layer modules - deprecated but kept for backward compatibility
from layers.ghost_layer import GhostLayer as LegacyGhostLayer
from layers.security_layer import SecurityLayer as LegacySecurityLayer
from layers.privacy_layer import PrivacyLayer as LegacyPrivacyLayer
from layers.stealth_layer import StealthLayer as LegacyStealthLayer
from layers.research_layer import ResearchLayer as LegacyResearchLayer
from layers.memory_layer import MemoryLayer as LegacyMemoryLayer
from layers.communication_layer import CommunicationLayer as LegacyCommunicationLayer
from layers.content_layer import ContentCleaner as LegacyContentCleaner
from layers.temporal_signal_layer import TemporalSignalLayer as LegacyTemporalSignalLayer

# Keep original imports working
from layers.layer_protocol import (
    Layer as LayerProtocol,
    LayerContext as LayerContextProtocol,
    LayerEvent as LayerEventProtocol,
    LayerStack as LayerStackProtocol,
)

from layers.layer_manager import (
    LayerManager,
    LayerStatus,
    LayerHealth,
    UnifiedCapabilitiesManager,
    create_layer_manager,
    get_layer_manager,
    create_capabilities_manager,
    get_capabilities_manager,
)

# Content layer utilities
from layers.content_layer import (
    CleaningResult as LegacyCleaningResult,
    ContentCleaner as LegacyContentCleanerModule,
    OutputFormat as LegacyOutputFormat,
    ResiliparseCleaner,
    SearchResultItem,
    SimpleHTMLCleaner,
    clean_html_tags,
    clean_search_result_url,
    extract_url_from_duckduckgo_redirect,
    extract_url_from_google_redirect,
    get_content_cleaner,
    parse_duckduckgo_results,
    parse_google_results,
)

# Temporal signal utilities
from layers.temporal_signal_layer import (
    TemporalEvent as LegacyTemporalEvent,
    TemporalScore as LegacyTemporalScore,
    TemporalSignalLayer as LegacyTemporalSignalLayerModule,
    TemporalEdgeCandidate,
    _KeyState,
    event_from_finding_like,
)

from layers.temporal_signal_runtime import (
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

from layers.temporal_signal_store import TemporalSignalStore

# Evasion pipeline
from layers.evasion_pipeline import (
    EvasionCategory,
    EvasionScript,
    FingerprintProfile as LegacyFingerprintProfile,
    ProfileGenerator as LegacyProfileGenerator,
    _EvasionScriptGenerator,
    compute_detection_score,
    generate_evasion_scripts,
)

# Hive coordination (deprecated)
from layers.hive_coordination import (
    ConnectedCoordinationSystem,
    CoordinationNode,
    CoordinationTask,
    TopologyType,
    CoordinationLayer as HiveCoordinationLayer,
)

# Stealth layer components
from layers.stealth_layer import (
    BehaviorPattern as LegacyBehaviorPattern,
    BehaviorSimulator as LegacyBehaviorSimulator,
    BrowserProfile,
    Chameleon,
    FingerprintConfig,
    FingerprintRandomizer,
    MouseMovement as LegacyMouseMovement,
    ScrollAction,
    SimulationConfig as LegacySimulationConfig,
)

# UA Rotator
from layers.ua_rotator import (
    UARotator,
    build_randomized_headers,
    get_random_ua,
    get_ua_for_profile,
    get_random_accept_language,
    get_random_accept_encoding,
)

# Memory layer components
from layers.memory_layer import (
    EntropyMaskingManager,
    MemoryLayer,
    RAMDiskConfig,
    RAMDiskManager,
    SharedMemoryBlock,
    SharedMemoryManager,
)

# Examples
from layers.examples.demos import (
    demo_connected_coordination,
    demo_smart_spawned_integration,
    run_all_demos,
)


# ─── Generic Layer Cached Factory ──────────────────────────────────────────────

_T = TypeVar("_T")


def _make_cached_layer_getter(
    layer_name: str,
    import_path: str,
    factory_call: str,
    singleton_args: tuple[()] = (),
) -> Callable[[], _T | None]:
    """
    Factory: create a @lru_cache'd layer getter with fail-soft import.
    """

    @functools.lru_cache(maxsize=1)
    def _cached_getter() -> _T | None:
        try:
            module_path, class_name = import_path.rsplit(".", 1)
            module = __import__(module_path, fromlist=[class_name])
            layer_cls = getattr(module, class_name)
            return layer_cls(*singleton_args)
        except Exception:
            return None

    return _cached_getter


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


# ─── Lazy Singleton Getters ────────────────────────────────────────────────────

def get_stealth_layer() -> LegacyStealthLayer | None:
    """Lazy singleton StealthLayer accessor."""
    return _stealth_layer_getter()


def get_content_layer() -> LegacyContentCleanerModule | None:
    """Lazy singleton ContentCleaner accessor."""
    return _content_layer_getter()


def get_ghost_layer() -> LegacyGhostLayer | None:
    """Lazy singleton GhostLayer accessor."""
    return _ghost_layer_getter()


@functools.lru_cache(maxsize=1)
def _communication_layer_cached() -> LegacyCommunicationLayer | None:
    """Cached CommunicationLayer instance."""
    try:
        from hledac.universal.layers.communication_layer import CommunicationLayer as _CL
        from hledac.universal.project_types import CommunicationConfig
        return _CL(config=CommunicationConfig())
    except Exception:
        return None


def get_communication_layer() -> LegacyCommunicationLayer | None:
    """Lazy singleton CommunicationLayer accessor."""
    return _communication_layer_cached()


# ─── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    # Core architecture (new)
    "Layer",
    "LayerContext",
    "LayerEvent",
    "LayerStack",
    "BaseLayer",
    "LayerRegistry",
    # Consolidated layers (new)
    "GhostLayer",
    "SecurityLayer",
    "MissionAudit",
    "AuditEntry",
    "ResearchLayer",
    "TemporalSignalLayer",
    "TemporalEvent",
    "TemporalScore",
    "CommunicationLayer",
    "ContentCleaner",
    "OutputFormat",
    "CleaningResult",
    "StealthLayer",
    "BehaviorSimulator",
    "BehaviorPattern",
    "ProfileGenerator",
    "FingerprintProfile",
    # Legacy modules (deprecated)
    "LegacyGhostLayer",
    "LegacySecurityLayer",
    "LegacyPrivacyLayer",
    "LegacyStealthLayer",
    "LegacyResearchLayer",
    "LegacyMemoryLayer",
    "LegacyCommunicationLayer",
    "LegacyContentCleaner",
    "LegacyTemporalSignalLayer",
    "LayerProtocol",
    "LayerContextProtocol",
    "LayerEventProtocol",
    "LayerStackProtocol",
    "LayerManager",
    "LayerStatus",
    "LayerHealth",
    "UnifiedCapabilitiesManager",
    "create_layer_manager",
    "get_layer_manager",
    "create_capabilities_manager",
    "get_capabilities_manager",
    # Content utilities
    "LegacyCleaningResult",
    "LegacyContentCleanerModule",
    "LegacyOutputFormat",
    "ResiliparseCleaner",
    "SearchResultItem",
    "SimpleHTMLCleaner",
    "clean_html_tags",
    "clean_search_result_url",
    "extract_url_from_duckduckgo_redirect",
    "extract_url_from_google_redirect",
    "get_content_cleaner",
    "parse_duckduckgo_results",
    "parse_google_results",
    # Temporal signal
    "LegacyTemporalEvent",
    "LegacyTemporalScore",
    "LegacyTemporalSignalLayerModule",
    "TemporalEdgeCandidate",
    "_KeyState",
    "event_from_finding_like",
    "TemporalSignalStore",
    "get_temporal_signal_layer",
    "reset_temporal_signal_layer",
    "get_temporal_signal_summary",
    "is_temporal_store_enabled",
    "get_temporal_signal_store",
    "load_temporal_signal_snapshot",
    "save_temporal_signal_snapshot",
    "close_temporal_signal_store",
    "build_temporal_priority_hints",
    # Evasion pipeline
    "EvasionCategory",
    "EvasionScript",
    "LegacyFingerprintProfile",
    "LegacyProfileGenerator",
    "compute_detection_score",
    "generate_evasion_scripts",
    # Hive coordination (deprecated)
    "ConnectedCoordinationSystem",
    "HiveCoordinationLayer",
    "CoordinationNode",
    "CoordinationTask",
    "TopologyType",
    # Stealth components
    "LegacyBehaviorPattern",
    "LegacyBehaviorSimulator",
    "BrowserProfile",
    "Chameleon",
    "FingerprintConfig",
    "FingerprintRandomizer",
    "LegacyMouseMovement",
    "ScrollAction",
    "LegacySimulationConfig",
    # UA Rotator
    "UARotator",
    "get_random_ua",
    "get_ua_for_profile",
    "get_random_accept_language",
    "get_random_accept_encoding",
    "build_randomized_headers",
    # Memory components
    "EntropyMaskingManager",
    "MemoryLayer",
    "RAMDiskConfig",
    "RAMDiskManager",
    "SharedMemoryBlock",
    "SharedMemoryManager",
    # Examples
    "demo_connected_coordination",
    "demo_smart_spawned_integration",
    "run_all_demos",
    # Lazy getters
    "get_stealth_layer",
    "get_content_layer",
    "get_ghost_layer",
    "get_communication_layer",
]
