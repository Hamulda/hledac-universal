"""
Tool Registry — Thin Facade.

This module now delegates to tools/registry.py (pure registration) and
tools/executor.py (async execution patterns).

Kept for backward compatibility — existing imports continue to work.
"""

from __future__ import annotations

# Lazy-load registry and executor symbols on first access.
# Deferred: from tools.registry import (ToolRegistry, Tool, ...)
# Deferred: from tools.executor import (ToolExecutor, ...)
# Avoids sys.path issues when pytest runs from hledac/universal/ as root.
_registry_cache: dict = {}
_executor_cache: dict = {}


def __getattr__(name: str):
    """Lazy-load re-exported symbols on first attribute access."""
    # Registry symbols
    _registry_symbols = (
        "ToolRegistry", "Tool", "CostModel", "CostSummary", "BudgetLimits",
        "RateLimits", "RiskLevel", "SourceReputation",
    )
    if name in _registry_symbols:
        if name not in _registry_cache:
            import importlib
            mod = importlib.import_module("tools.registry")
            _registry_cache[name] = getattr(mod, name)
        return _registry_cache[name]

    # Executor symbols
    _executor_symbols = (
        "ToolExecutor", "execute_dns_tunnel_sync", "create_default_registry",
        "WebSearchArgs", "WebSearchResult", "EntityExtractionArgs",
        "EntityExtractionResult", "AcademicSearchArgs", "AcademicSearchResult",
        "FileReadArgs", "FileReadResult", "FileWriteArgs", "FileWriteResult",
        "PythonExecuteArgs", "PythonExecuteResult", "DNSTunnelCheckArgs",
        "DNSTunnelCheckResult",
    )
    if name in _executor_symbols:
        if name not in _executor_cache:
            import importlib
            mod = importlib.import_module("tools.executor")
            _executor_cache[name] = getattr(mod, name)
        return _executor_cache[name]

    raise AttributeError(name)


# Task handler registry (unchanged — pure decorator pattern)
_TASK_HANDLERS: dict[str, callable] = {}
_HANDLERS_LOADED: bool = False


def register_task(task_type: str) -> callable:
    """Decorator for registering task handlers."""
    def decorator(fn: callable) -> callable:
        _TASK_HANDLERS[task_type] = fn
        return fn
    return decorator


def get_task_handler(task_type: str) -> callable | None:
    """Lazy-load handlers on first call."""
    global _HANDLERS_LOADED
    if not _HANDLERS_LOADED:
        _HANDLERS_LOADED = True
        try:
            import importlib
            importlib.import_module("hledac.universal.discovery.ti_feed_adapter")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[REGISTRY] Handler load warning: {e}")
    return _TASK_HANDLERS.get(task_type)


def list_registered_tasks() -> list[str]:
    """Return list of registered task type names."""
    return list(_TASK_HANDLERS.keys())


# Sprint F3.11: Read-Side Metadata Seam for Dispatch Preview
TASK_TYPE_TO_TOOL_PREVIEW: dict[str, str] = {
    "cve_to_github": "python_execute",
    "cve_to_academic": "python_execute",
    "ip_to_ct": "web_search",
    "ip_to_greynoise": "web_search",
    "shodan_enrich": "web_search",
    "domain_to_dns": "web_search",
    "domain_to_wayback": "web_search",
    "domain_to_pdns": "web_search",
    "domain_to_ct": "web_search",
    "ahmia_search": "web_search",
    "rdap_lookup": "web_search",
    "hash_to_mb": "web_search",
    "wayback_search": "web_search",
    "commoncrawl_search": "web_search",
    "paste_keyword_search": "web_search",
    "github_dork": "web_search",
    "multi_engine_search": "web_search",
    "hypothesis_probe": "web_search",
}


def get_task_tool_preview_mapping() -> dict[str, str]:
    """Return read-only task_type → tool_name preview mapping."""
    return dict(TASK_TYPE_TO_TOOL_PREVIEW)


# Sprint F6b: Triad-Side Dormant Provider Mirror Seam
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class DeepResearchProviderMirror:
    """Triad-side read-only mirror for DeepResearch provider admission metadata."""
    mirror_module: str = "tool_registry"
    owning_module: str = "enhanced_research"
    triad_authority_exists: bool = True
    deepresearch_napojen: bool = False

    blockers: Tuple[str, ...] = (
        "Session seams (BudgetManager, EvidenceLog): exists, not wired to DeepResearch",
        "Security gate (PII gate): exists, not wired to DeepResearch",
        "Minimal grounding seam (ProviderRequest/ProviderResult): exists, not wired to DeepResearch",
        "Transport plane (FetchCoordinator): exists, not wired to DeepResearch runtime",
    )

    is_dormant: bool = True
    is_not_execution_authority: bool = True
    is_not_activation: bool = True

    @property
    def provider_side_truth(self) -> str:
        return "enhanced_research.DEEP_RESEARCH_ADMISSION (singleton)"

    @property
    def admission_summary(self) -> str:
        lines = [
            "Triad-Side Mirror for DeepResearch Provider Admission",
            f"Mirror Module: {self.mirror_module}",
            f"Provider-Side Owner: {self.owning_module}",
            f"Canonical Truth: {self.provider_side_truth}",
            "",
            f"Triad Authority Exists: {self.triad_authority_exists}",
            f"DeepResearch Napojen: {self.deepresearch_napojen}",
            f"Dormant: {self.is_dormant}",
            f"Execution Authority: {self.is_not_execution_authority}",
            "",
            "Blockers (read-only mirror from provider-side):",
        ]
        for b in self.blockers:
            lines.append(f"  - {b}")
        return "\n".join(lines)


DEEP_RESEARCH_PROVIDER_MIRROR = DeepResearchProviderMirror()


__all__ = [
    # Core classes
    "ToolRegistry",
    "Tool",
    "CostModel",
    "RateLimits",
    "RiskLevel",
    # Schema classes
    "WebSearchArgs",
    "WebSearchResult",
    "EntityExtractionArgs",
    "EntityExtractionResult",
    "AcademicSearchArgs",
    "AcademicSearchResult",
    "FileReadArgs",
    "FileReadResult",
    "FileWriteArgs",
    "FileWriteResult",
    "PythonExecuteArgs",
    "PythonExecuteResult",
    # Factory
    "create_default_registry",
    # Sprint 8VF
    "register_task",
    "get_task_handler",
    "list_registered_tasks",
    # Sprint F3.11
    "TASK_TYPE_TO_TOOL_PREVIEW",
    "get_task_tool_preview_mapping",
    # Sprint F6b
    "DeepResearchProviderMirror",
    "DEEP_RESEARCH_PROVIDER_MIRROR",
]