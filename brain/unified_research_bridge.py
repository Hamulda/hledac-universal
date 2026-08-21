"""
Canonical bridge: UnifiedAIOrchestrator → UnifiedResearchEngine.
Moved from compat/core_unified_ai_orchestrator.py (F350M-R A-01).

Provides real implementation by bridging to enhanced_research.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.brain.unified_embedding_manager import UnifiedEmbeddingManager

logger = logging.getLogger(__name__)

_EngineCls: type[UnifiedEmbeddingManager] | None = None


def _get_engine_cls() -> type[UnifiedEmbeddingManager] | None:
    """Lazy-load engine class (only used at runtime, TYPE_CHECKING safe)."""
    global _EngineCls
    if _EngineCls is None:
        try:
            from hledac.universal.brain.unified_embedding_manager import UnifiedEmbeddingManager

            _EngineCls = UnifiedEmbeddingManager
        except ImportError:
            return None
    return _EngineCls


class ResearchResult:
    """Result container for research operations."""

    __slots__ = ("summary", "confidence_score", "total_sources_found", "findings", "coverage_score")

    def __init__(
        self,
        summary: str = "",
        confidence: float = 0.0,
        sources: int = 0,
        findings: list[str] | None = None,
        coverage: float = 0.0,
    ) -> None:
        self.summary = summary
        self.confidence_score = confidence
        self.total_sources_found = sources
        self.findings = findings or []
        self.coverage_score = coverage


class UnifiedAIOrchestrator:
    """
    Bridge: delegates process_request() → UnifiedResearchEngine.deep_research().

    research_coordinator.py expects:
    - __init__(*args, **kwargs)  — no raises
    - async initialize()         — optional
    - async process_request(dict) -> dict  — returns {'summary': str, 'confidence': float, ...}
    - async cleanup()             — optional
    """

    __slots__ = ("_engine", "_initialized")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._engine: Any = None
        self._initialized: bool = False
        self._engine_cls = _get_engine_cls()
        logger.debug("UnifiedAIOrchestrator: bridge initialized")

    async def initialize(self) -> None:
        """Initialize the underlying engine."""
        if self._initialized:
            return
        try:
            if self._engine_cls is not None:
                self._engine = self._engine_cls()
                self._initialized = True
                logger.info("UnifiedAIOrchestrator: engine initialized")
        except Exception as e:
            logger.warning(f"UnifiedAIOrchestrator: init failed: {e}")
            self._initialized = False

    async def process_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """
        Bridge process_request() → deep_research().

        research_coordinator.py sends:
            {'query': str, 'operation_type': str, 'confidence_threshold': float,
             'priority': int, 'metadata': dict}

        Returns:
            {'summary': str, 'confidence': float, 'sources_used': int, 'findings': list}
        """
        if not self._initialized or self._engine is None:
            await self.initialize()
        if self._engine is None:
            return {"summary": "", "confidence": 0.0, "sources_used": 0, "findings": []}

        query = request.get("query", "")
        depth_arg = request.get("depth") or request.get("research_depth")
        max_results = request.get("max_results", 50)

        try:
            result = await self._engine.deep_research(
                query=query,
                depth=depth_arg,
                query_type=None,
                max_results=max_results,
            )
            return {
                "summary": getattr(result, "summary", "") or _extract_summary(result),
                "confidence": getattr(result, "confidence_score", 0.5),
                "sources_used": getattr(result, "total_sources_found", 0),
                "findings": getattr(result, "findings", []) or _extract_findings(result),
                "coverage_score": getattr(result, "coverage_score", 0.0),
            }
        except Exception as e:
            logger.error(f"UnifiedAIOrchestrator.process_request failed: {e}")
            return {"summary": "", "confidence": 0.0, "sources_used": 0, "findings": [], "error": str(e)}

    async def cleanup(self) -> None:
        """Cleanup engine resources."""
        if self._engine and hasattr(self._engine, "cleanup"):
            try:
                await self._engine.cleanup()
            except Exception as e:
                logger.warning(f"UnifiedAIOrchestrator cleanup error: {e}")
        self._engine = None
        self._initialized = False


def _extract_summary(result: Any) -> str:
    """Extract summary from result object."""
    if hasattr(result, "query"):
        findings_count = len(getattr(result, "findings", []) or [])
        return f"Research on '{result.query}' — {findings_count} findings"
    return str(result)


def _extract_findings(result: Any) -> list[str]:
    """Extract findings list from result object."""
    findings = getattr(result, "findings", None)
    if findings is not None:
        return findings
    fused = getattr(result, "fused_results", None)
    if fused:
        return fused
    return []
