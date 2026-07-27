"""
_lazy_index — Internal helpers for hledac.universal __getattr__ implementation.

This module provides _build_index() and related helpers that are used
by hledac.universal.__getattr__ (PEP 562 lazy loading).

PEP 562 __getattr__ is already defined in __init__.py.
This module extracts the complex index-building logic for maintainability.

Usage:
    from hledac.universal._lazy_index import build_module_index
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


def _scan_module_public_attrs(mod_path: str) -> list[str]:
    """
    Scan a module for public attribute names.

    Returns list of attribute names that should be exported.
    Priority: __all__ > dir() filtering
    """
    try:
        mod = import_module(mod_path)
    except (ImportError, ModuleNotFoundError):
        return []

    # __all__ takes precedence — it's the authoritative public API list
    all_list: list[str] | None = getattr(mod, "__all__", None)
    if all_list is not None:
        return [n for n in all_list if not n.startswith("_")]

    # Fallback: public names via dir() (filter dunders)
    return [n for n in dir(mod) if not n.startswith("_")]


def build_module_index(
    auto_module_paths: list[str],
    explicit_attrs: dict[str, frozenset[str]],
) -> dict[str, str]:
    """
    Build the attribute→module index for PEP 562 lazy loading.

    Phase 1: Populate from explicit whitelists (authoritative, zero imports)
    Phase 2: Scan remaining modules for __all__ contributions

    Args:
        auto_module_paths: List of module paths to potentially scan
        explicit_attrs: Module path → frozenset of attribute names

    Returns:
        Dict mapping attribute name → module path
    """
    idx: dict[str, str] = {}

    # Phase 1: explicit whitelists — no imports needed, no side effects
    for mod_path, names in explicit_attrs.items():
        for name in names:
            idx.setdefault(name, mod_path)

    # Phase 2: scan remaining modules for __all__ contributions
    # Only runs for modules without explicit entries
    for mod_path in auto_module_paths:
        if mod_path in explicit_attrs:
            continue  # already covered by phase 1

        names = _scan_module_public_attrs(mod_path)
        for name in names:
            idx.setdefault(name, mod_path)

    return idx
