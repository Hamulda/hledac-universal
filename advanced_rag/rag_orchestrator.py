"""
Bridge: RAGOrchestrator for research_coordinator.py compatibility.
Provides research_and_answer() interface expected by research_coordinator.py.

research_coordinator.py imports:
    from hledac.advanced_rag.rag_orchestrator import RAGOrchestrator

This bridge delegates to the actual RAG implementation from
hledac.advanced_rag.rag_orchestrator (which exists in hledac/ directory).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RAGOrchestrator:
    """
    Bridge: wraps hledac.advanced_rag.rag_orchestrator to provide
    research_and_answer() interface expected by research_coordinator.py.

    research_coordinator.py calls:
        await self._rag_orchestrator.research_and_answer(
            query=query,
            confidence_threshold=decision.confidence,
            priority=decision.priority
        )
    """

    def __init__(self, *args, **kwargs) -> None:
        self._delegate: Any | None = None
        self._initialized: bool = False

    async def initialize(self) -> None:
        """Lazy-init the underlying RAG implementation."""
        if self._initialized:
            return
        try:
            from hledac.advanced_rag.rag_orchestrator import RAGOrchestrator as BaseRAG
            self._delegate = BaseRAG()
            self._initialized = True
            logger.info("RAGOrchestrator bridge: initialized delegate")
        except Exception as e:
            logger.warning(f"RAGOrchestrator bridge: init failed: {e}")
            self._initialized = False

    async def research_and_answer(
        self,
        query: str,
        confidence_threshold: float = 0.7,
        priority: int = 5
    ) -> dict[str, Any]:
        """
        Bridge research_and_answer() → delegate.process_query().

        research_coordinator.py expects returns:
            {'sources': [...], 'answer': str, 'confidence': float,
             'tokens_used': int, ...}
        """
        if not self._initialized or self._delegate is None:
            await self.initialize()

        if self._delegate is None:
            return {
                "sources": [],
                "answer": "RAG engine unavailable",
                "confidence": 0.0,
                "tokens_used": 0,
                "error": "delegate not initialized"
            }

        try:
            result = await self._delegate.process_query(
                query=query,
                max_context_length=4000
            )

            return {
                "sources": getattr(result, "sources", []),
                "answer": getattr(result, "answer", ""),
                "confidence": getattr(result, "confidence", confidence_threshold),
                "tokens_used": 0,
                "stages_completed": getattr(result, "stages_completed", []),
                "metadata": {
                    "processing_time": getattr(result, "processing_time", 0.0),
                    "validation_score": getattr(result, "validation_score", None),
                    "compressed": getattr(result, "compressed", False),
                    "fallback_used": getattr(result, "fallback_used", False),
                }
            }
        except Exception as e:
            logger.error(f"RAGOrchestrator.research_and_answer failed: {e}")
            return {
                "sources": [],
                "answer": f"RAG processing error: {str(e)}",
                "confidence": 0.0,
                "tokens_used": 0,
                "error": str(e)
            }

    async def cleanup(self) -> None:
        """Cleanup delegate resources."""
        if self._delegate and hasattr(self._delegate, "cleanup"):
            try:
                await self._delegate.cleanup()
            except Exception as e:
                logger.warning(f"RAGOrchestrator cleanup error: {e}")
        self._delegate = None
        self._initialized = False


__all__ = ["RAGOrchestrator"]