"""
Sprint F206U — Memory Authority Boundary

Canonical-memory authority map for hledac/universal.
No runtime side effects. Pure constants + helpers.

VERDICT (Option C+):
  resource_governor.py          = canonical UMA policy / hysteresis / runtime governance
  utils/uma_budget.py           = raw sampler only (get_uma_snapshot, get_uma_pressure_level)
  utils/mlx_cache.py            = MLX cache helper, NOT a policy owner
  layers/memory_layer.py        = layer-system memory surface, not canonical sprint owner
  layers/layer_manager.py       = layer-manager / M1MemoryOptimizer, not canonical policy governor
  coordinators/memory_coordinator.py = allocator/coordinator (if used), not Uma policy owner
  legacy/autonomous_orchestrator._MemoryManager = legacy/AO-only, not canonical sprint path
  legacy/autonomous_orchestrator._MemoryCoordinator = legacy/AO-only, not canonical sprint path
  autonomous_orchestrator.py (facade) = no memory classes; legacy compatibility shim
  coordinator_registry.py = coordinator name→class registry; NOT a memory authority
  core/resource_governor.py = CANONICAL UMA POLICY OWNER

Canonical path (core/__main__.py → runtime/sprint_scheduler.py):
  ✓ imports resource_governor (sample_uma_status, evaluate_uma_state)
  ✓ imports sprint_lifecycle (UmaWatchdog)
  ✗ does NOT import autonomous_orchestrator (facade or legacy)
  ✗ does NOT import LayerManager
  ✗ does NOT import MemoryLayer
  ✗ does NOT import coordinator_registry.UniversalMemoryCoordinator
  ✗ does NOT call _MemoryManager or _MemoryCoordinator
"""

from __future__ import annotations

import copy
from typing import Final

# ─── Authority Map ────────────────────────────────────────────────────────────

MEMORY_AUTHORITY: Final[dict[str, str]] = {
    "core/resource_governor.py": "canonical_governor",
    #
    # Raw sampler — no policy, no hysteresis. Caller decides what to do with data.
    "utils/uma_budget.py": "raw_sampler",
    #
    # MLX cache helper — not a memory policy owner.
    # Read-only diagnostics surface for metal/wired limits.
    "utils/mlx_cache.py": "mlx_cache_helper",
    #
    # Layer-system memory surface — not canonical Uma policy owner.
    # Provides MemoryLayer.get_memory_pressure() for layer consumers.
    # NOT called by canonical sprint path (core/__main__.py → sprint_scheduler.py).
    "layers/memory_layer.py": "layer_system",
    #
    # Layer-manager cleanup utility — not canonical Uma governor.
    # M1MemoryOptimizer.force_cleanup() is a layer-internal operation.
    # NOT called by canonical sprint path.
    "layers/layer_manager.py::M1MemoryOptimizer": "layer_memory",
    #
    # Allocator/coordinator — not Uma policy owner.
    # aggressive_cleanup() is its own operation; not governed by resource_governor thresholds.
    # NOT called by canonical sprint path (no imports from core/__main__.py or sprint_scheduler).
    "coordinators/memory_coordinator.py": "allocator",
    #
    # Legacy/AO-only — not part of canonical sprint path.
    # Used internally within legacy/autonomous_orchestrator.py execution context.
    "legacy/autonomous_orchestrator.py::_MemoryManager": "legacy_ao",
    "legacy/autonomous_orchestrator.py::_MemoryCoordinator": "legacy_ao",
    #
    # Facade shim — no memory classes. Legacy compatibility only.
    "autonomous_orchestrator.py": "facade_only",
    #
    # Coordinator registry — name→class mapper. NOT a memory authority.
    "coordinators/coordinator_registry.py": "registry_only",
}

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_memory_authority_status() -> dict[str, str]:
    """Return a copy of the memory authority map."""
    return copy.deepcopy(MEMORY_AUTHORITY)


def classify_memory_symbol(symbol_or_path: str) -> str:
    """
    Classify a symbol or file path into a memory authority role.

    Returns one of: canonical_governor | raw_sampler | mlx_cache_helper |
                   layer_system | layer_memory | allocator | legacy_ao |
                   facade_only | registry_only | unknown
    """
    s = symbol_or_path.strip()

    # Exact path matches
    if s in MEMORY_AUTHORITY:
        return MEMORY_AUTHORITY[s]

    # Suffix matches for class-level entries (e.g. "layers/layer_manager.py::M1MemoryOptimizer")
    for key, role in MEMORY_AUTHORITY.items():
        if "::" in key and s == key:
            return role

    # Partial path matches
    for key, role in MEMORY_AUTHORITY.items():
        if not key.startswith("core/") and not key.startswith("utils/") and not key.startswith("layers/") and not key.startswith("coordinators/") and not key.startswith("legacy/"):
            continue
        if s.startswith(key.split("::")[0]) or key.startswith(s):
            return role

    # Keyword-based fallback classification
    if "resource_governor" in s:
        return "canonical_governor"
    if "uma_budget" in s:
        return "raw_sampler"
    if "mlx_cache" in s:
        return "mlx_cache_helper"
    if "LayerManager" in s or "M1MemoryOptimizer" in s:
        return "layer_memory"
    if "MemoryLayer" in s:
        return "layer_system"
    if "_MemoryManager" in s or "_MemoryCoordinator" in s:
        return "legacy_ao"
    if "autonomous_orchestrator" in s:
        return "legacy_ao"
    if "UniversalMemoryCoordinator" in s:
        return "allocator"
    if "memory_coordinator" in s:
        return "allocator"
    if "coordinator_registry" in s:
        return "registry_only"

    return "unknown"