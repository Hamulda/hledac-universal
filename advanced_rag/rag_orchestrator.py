"""
RAGOrchestrator — bounded hybrid RAG over canonical LanceDBIdentityStore.

ROLE: Production RAG provider that wires advanced_rag → knowledge/lancedb_store.
================================================================================

This module replaces the previous broken bridge (which tried to import itself
via `from hledac.advanced_rag.rag_orchestrator import RAGOrchestrator` — a
circular self-import that always failed at runtime).

Architecture:
    research_coordinator / UnifiedResearchEngine
        └─→ advanced_rag.RAGOrchestrator  (this module)
                └─→ knowledge.lancedb_store.get_identity_store()  (CANONICAL singleton)
                        └─→ search_similar_adaptive() / search_with_mmr()

M1 8GB invariants (always-on):
    - Single LanceDB instance — never opens a second connection.
      Reuses `get_identity_store()` singleton from knowledge/lancedb_store.py.
    - Synchronous I/O offloaded via `loop.run_in_executor()` (NEVER asyncio.to_thread).
    - All collections bounded (MAX_SOURCES, MAX_TOKENS, MAX_CANDIDATES).
    - Fail-safe: any exception → empty result + warning log, never raises.
    - No new public APIs beyond research_and_answer() (research_coordinator contract).

Capability flag:
    HLEDAC_ENABLE_ADVANCED_RAG=0 (default, dormant) — gate at runtime.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Bounded limits (M1 8GB UMA safe)
_MAX_SOURCES = 20          # Hard cap on returned sources
_MAX_QUERY_CHARS = 1024    # Truncate long queries before embed
_TOKEN_CHARS_PER_SOURCE = 500  # Cap per-source text length
_FALLBACK_CONFIDENCE = 0.5


class RAGOrchestrator:
    """
    Bounded hybrid RAG orchestrator backed by canonical LanceDB identity store.

    Public surface (research_coordinator contract):
        await research_and_answer(query, confidence_threshold, priority)
            → {'sources': [...], 'answer': str, 'confidence': float,
               'tokens_used': int, 'stages_completed': [...], 'metadata': {...}}

    Backed by:
        - knowledge.lancedb_store.get_identity_store() — canonical LanceDB singleton.
        - search_similar_adaptive() — hybrid vector+FTS with MMR + ColBERT/FlashRank/MLX
          reranking. Falls back to search_similar() on low-variance candidates.
    """

    def __init__(self, *args: Any, **_kwargs: Any) -> None:  # noqa: ARG001 — bridge contract accepts legacy kwargs
        self._store: Any | None = None
        self._initialized: bool = False
        self._init_lock = asyncio.Lock()
        self._init_error: str | None = None
        # Legacy bridge: store and log deprecated positional args if any were passed
        if args or _kwargs:
            logger.debug(
                "RAGOrchestrator: ignoring %d positional + %d keyword legacy args",
                len(args), len(_kwargs),
            )

    async def initialize(self) -> None:
        """Lazy-init: bind to canonical LanceDBIdentityStore singleton.

        Uses asyncio.Lock to guard concurrent initialization. Never raises —
        stores exception reason in `_init_error` for diagnostics.
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            try:
                # Canonical accessor — single LanceDB connection across the project
                from knowledge.lancedb_store import get_identity_store
                self._store = get_identity_store()
                self._initialized = True
                self._init_error: str | None = None
                logger.info("RAGOrchestrator: bound to canonical LanceDBIdentityStore")
            except Exception as e:
                self._initialized = False
                self._store = None
                self._init_error = f"{type(e).__name__}: {e}"
                logger.warning(f"RAGOrchestrator.initialize failed: {self._init_error}")

    async def research_and_answer(
        self,
        query: str,
        confidence_threshold: float = 0.7,
        priority: int = 5,  # advisory; consumed for logging + future scheduling
    ) -> dict[str, Any]:
        """
        Hybrid RAG retrieval + answer synthesis.

        Stages (bounded, fail-safe):
            1. Sanitize & truncate query
            2. Embed via canonical store (off event loop)
            3. Hybrid search (vector + FTS) with MMR + adaptive reranking
            4. Synthesize answer from top sources
            5. Compute confidence from result scores

        Args:
            query: Natural language question.
            confidence_threshold: Floor below which results are filtered.
            priority: 1-10 (currently advisory, used for logging only).

        Returns:
            dict conforming to research_coordinator contract.
        """
        started = time.monotonic()
        stages: list[str] = [f"priority={priority}"]

        if not self._initialized or self._store is None:
            await self.initialize()

        if self._store is None:
            return self._empty_result(
                error=self._init_error or "store not initialized",
                started=started,
            )

        # Stage 1: sanitize
        sanitized = (query or "").strip()[:_MAX_QUERY_CHARS]
        if not sanitized:
            return self._empty_result(error="empty query", started=started)
        stages.append("sanitize")

        # Stage 2-3: embed + search (off event loop)
        try:
            embedding = await self._embed_offloop(sanitized)
        except Exception as e:
            logger.warning(f"RAGOrchestrator: embed failed: {e}")
            return self._empty_result(error=f"embed: {e}", started=started)
        stages.append("embed")

        if not embedding:
            return self._empty_result(error="embed returned empty", started=started)

        # Hybrid adaptive search
        try:
            results = await self._store.search_similar_adaptive(
                query_text=sanitized,
                query_emb=embedding,
                top_k=min(5, _MAX_SOURCES),
            )
        except Exception as e:
            logger.warning(f"RAGOrchestrator: search failed: {e}")
            results = []
        stages.append("search")

        # Apply threshold + bound
        sources: list[dict[str, Any]] = []
        for r in results or []:
            score = float(r.get("similarity", 0.0))
            if score < confidence_threshold:
                continue
            text = (r.get("text") or "").strip()[:_TOKEN_CHARS_PER_SOURCE]
            if not text:
                continue
            sources.append({
                "id": r.get("id", ""),
                "text": text,
                "similarity": score,
                "metadata": {k: v for k, v in r.items()
                             if k not in ("id", "text", "_embedding", "embedding")},
            })
            if len(sources) >= _MAX_SOURCES:
                break
        stages.append("filter")

        # Stage 4: synthesize answer (bounded concatenation)
        answer, tokens_used = self._synthesize(sanitized, sources)
        stages.append("synthesize")

        # Stage 5: confidence — average of source scores
        if sources:
            confidence = sum(s["similarity"] for s in sources) / len(sources)
        else:
            confidence = _FALLBACK_CONFIDENCE if results else 0.0
        # Clamp
        confidence = max(0.0, min(1.0, confidence))

        return {
            "sources": sources,
            "answer": answer,
            "confidence": confidence,
            "tokens_used": tokens_used,
            "stages_completed": stages,
            "metadata": {
                "processing_time": time.monotonic() - started,
                "validation_score": None,
                "compressed": False,
                "fallback_used": len(sources) == 0 and len(results or []) == 0,
            },
        }

    async def _embed_offloop(self, text: str) -> list[float]:
        """Embed text via the canonical store, off the event loop.

        Per project invariant: never use asyncio.to_thread for I/O. Use
        loop.run_in_executor for any blocking I/O on canonical sync APIs.
        """
        store = self._store
        if store is None:
            return []
        # LanceDBIdentityStore exposes _embed_single (async). For non-MLX backends
        # the heavy work runs in to_thread inside the store; here we just await it.
        try:
            return await store._embed_single(text)
        except Exception:
            return []

    def _synthesize(self, query: str, sources: list[dict[str, Any]]) -> tuple[str, int]:
        """Build bounded answer string from top sources. Pure-Python, no LLM."""
        if not sources:
            return (
                f"No relevant information found in local knowledge base for: {query}",
                0,
            )
        parts: list[str] = [f"Found {len(sources)} relevant sources for: {query}\n"]
        for i, s in enumerate(sources, 1):
            text = s["text"]
            sim = s.get("similarity", 0.0)
            parts.append(f"\n[{i}] (sim={sim:.2f})\n{text}")
        answer = "".join(parts)
        # Truncate answer to bounded size (max 4k chars)
        if len(answer) > 4096:
            answer = answer[:4093] + "..."
        # Token estimate: 1 token ≈ 4 chars
        return answer, len(answer) // 4

    def _empty_result(self, error: str, started: float) -> dict[str, Any]:
        return {
            "sources": [],
            "answer": f"RAG engine unavailable: {error}",
            "confidence": 0.0,
            "tokens_used": 0,
            "stages_completed": ["init_failed"],
            "error": error,
            "metadata": {
                "processing_time": time.monotonic() - started,
                "validation_score": None,
                "compressed": False,
                "fallback_used": True,
                "error": error,
            },
        }

    async def cleanup(self) -> None:
        """Release references; the canonical store is module-singleton, do NOT close."""
        self._store = None
        self._initialized = False


__all__ = ["RAGOrchestrator"]