"""
Infrastructure komponenty pro UniversalResearchOrchestrator.

Obsahuje:
- SystemMonitor: System monitoring
- PluginManager: Dynamic plugin system (integrated from kernel/plugin_loader.py)
- PluginStatus, PluginType: Plugin enums
- PluginMetadata, LoadedPlugin: Plugin dataclasses
"""

from .plugin_manager import (
    LoadedPlugin,
    PluginManager,
    PluginMetadata,
    PluginStatus,
    PluginType,
    create_plugin_manager,
    load_all_plugins,
)
from .system_monitor import SystemMonitor, SystemState

__all__ = [
    # System monitoring
    "SystemMonitor",
    "SystemState",
    # Plugin management
    "PluginManager",
    "PluginStatus",
    "PluginType",
    "PluginMetadata",
    "LoadedPlugin",
    "create_plugin_manager",
    "load_all_plugins",
]
