"""
Execution komponenty pro UniversalResearchOrchestrator.

Obsahuje:
- GhostExecutor: Vykonávací engine s 14+ akcemi
- ActionRegistry: Registr akcí
"""
from __future__ import annotations


from .ghost_executor import ActionType, GhostExecutor

__all__ = ["GhostExecutor", "ActionType"]
