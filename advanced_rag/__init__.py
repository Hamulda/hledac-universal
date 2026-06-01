"""
Bridge: research_coordinator → hledac.advanced_rag.rag_orchestrator.

research_coordinator.py imports from:
    from .rag_orchestrator import RAGOrchestrator

This file re-exports the bridge from hledac/advanced_rag/rag_orchestrator.py
which wraps the implementation to provide research_and_answer() interface.
"""
from __future__ import annotations

from .rag_orchestrator import RAGOrchestrator

__all__ = ["RAGOrchestrator"]