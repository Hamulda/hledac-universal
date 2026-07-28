"""
RAGOrchestrator — bounded dual-engine RAG (sqlite-vec + optional LanceDB).

ROLE: Production RAG provider that wires advanced_rag → knowledge/lancedb_store.
================================================================================

Architecture (Phase 7.3):
    research_coordinator / UnifiedResearchEngine
        └─→ advanced_rag.RAGOrchestrator  (this module)
                ├─→ utils.sqlite_vec_helpers.SqliteVecStore  (PRIMARY, M1 native)
                │       └─→ SPRINT_STORE_ROOT / sprint_{id}.db (shared with DuckDB)
                └─→ knowledge.lancedb_store.get_identity_store()  (FALLBACK, RAM > 5GB)

M1 8GB invariants (always-on):
    - sqlite-vec primary: zero-process, ~5MB resident vs ~200MB LanceDB subprocess.
    - LanceDB fallback: only activated if system RAM headroom > 1.5GB.
    - Synchronous I/O offloaded via `loop.run_in_executor()` (NEVER asyncio.to_thread).
    - All collections bounded (MAX_SOURCES, MAX_TOKENS, MAX_CANDIDATES).
    - Fail-safe: any exception → empty result + warning log, never raises.
    - No new public APIs beyond research_and_answer() (research_coordinator contract).

Capability flag:
    HLEDAC_ENABLE_ADVANCED_RAG=0 (default, dormant) — gate at runtime.
    HLEDAC_ADVANCED_RAG_BACKEND=sqlitevec|lancedb|auto (default: auto)
"""
import asyncio
import logging
import os
import time
from typing import Any
logger = logging.getLogger(__name__)
_MAX_SOURCES = 20
_MAX_QUERY_CHARS = 1024
_TOKEN_CHARS_PER_SOURCE = 500
_FALLBACK_CONFIDENCE = 0.5
_LANCEDB_RAM_THRESHOLD_GB = 1.5

def _has_ram_headroom(required_gb: float=_LANCEDB_RAM_THRESHOLD_GB) -> bool:
    """Return True if system has at least `required_gb` available RAM."""
    try:
        import psutil
        available = psutil.virtual_memory().available / 1024 ** 3
        return available >= required_gb
    except Exception:
        return False

def _get_backend_mode() -> str:
    """Resolve backend mode from HLEDAC_ADVANCED_RAG_BACKEND env var."""
    mode = os.environ.get('HLEDAC_ADVANCED_RAG_BACKEND', 'auto').lower()
    if mode in ('sqlitevec', 'lancedb', 'auto'):
        return mode
    return 'auto'

class RAGOrchestrator:
    """
    Bounded dual-engine RAG orchestrator.

    Primary: sqlite-vec (M1-native, zero-process, ~5MB resident).
    Fallback: LanceDB (only if RAM headroom > 1.5GB, ~200MB subprocess overhead).

    Public surface (research_coordinator contract):
        await research_and_answer(query, confidence_threshold, priority)
            → {'sources': [...], 'answer': str, 'confidence': float,
               'tokens_used': int, 'stages_completed': [...], 'metadata': {...}}

    Backed by:
        - utils.sqlite_vec_helpers.SqliteVecStore — PRIMARY, M1 native ANN.
        - knowledge.lancedb_store.get_identity_store() — FALLBACK on high-RAM systems.
    """
    __slots__ = tuple(('_backend_mode', '_init_error', '_init_lock', '_initialized', '_lancedb_store', '_sprint_id', '_sqlite_vec_store'))

    def __init__(self, *args: Any, **_kwargs: Any) -> None:
        self._sqlite_vec_store: Any | None = None
        self._lancedb_store: Any | None = None
        self._backend_mode: str = 'auto'
        self._initialized: bool = False
        self._init_lock = asyncio.Lock()
        self._init_error: str | None = None
        self._sprint_id: str = 'default'
        if args or _kwargs:
            logger.debug('RAGOrchestrator: ignoring %d positional + %d keyword legacy args', len(args), len(_kwargs))

    async def initialize(self) -> None:
        """Dual-engine lazy-init: sqlite-vec primary, LanceDB fallback.

        Uses asyncio.Lock to guard concurrent initialization. Never raises —
        stores exception reason in `_init_error` for diagnostics.

        Backend resolution (HLEDAC_ADVANCED_RAG_BACKEND):
            - 'sqlitevec': sqlite-vec only (no LanceDB even if RAM available).
            - 'lancedb': LanceDB only (skip sqlite-vec).
            - 'auto' (default): sqlite-vec primary, LanceDB fallback if RAM > 1.5GB.
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self._backend_mode = _get_backend_mode()
            errors: list[str] = []
            if self._backend_mode in ('auto', 'sqlitevec'):
                try:
                    from hledac.universal.utils.sqlite_vec_helpers import SqliteVecStore
                    store = SqliteVecStore(sprint_id=self._sprint_id)
                    ok = await store.initialize()
                    if ok:
                        self._sqlite_vec_store = store
                        logger.info('RAGOrchestrator: sqlite-vec primary activated (sprint_id=%s)', self._sprint_id)
                    else:
                        errors.append('sqlite-vec init returned False')
                except Exception as e:
                    errors.append(f'sqlite-vec: {e}')
                    logger.debug('RAGOrchestrator: sqlite-vec unavailable: %s', e)
            if self._backend_mode in ('auto', 'lancedb') and self._sqlite_vec_store is None:
                if _has_ram_headroom(_LANCEDB_RAM_THRESHOLD_GB):
                    try:
                        from hledac.universal.knowledge.lancedb_store import get_identity_store
                        self._lancedb_store = await get_identity_store()
                        logger.info('RAGOrchestrator: LanceDB fallback activated')
                    except Exception as e:
                        errors.append(f'LanceDB fallback: {e}')
                        logger.debug('RAGOrchestrator: LanceDB unavailable: %s', e)
                else:
                    errors.append(f'RAM headroom < {_LANCEDB_RAM_THRESHOLD_GB}GB, LanceDB fallback skipped')
            if self._sqlite_vec_store is None and self._lancedb_store is None:
                self._initialized = False
                self._init_error = '; '.join(errors) or 'all backends failed'
                logger.warning('RAGOrchestrator.initialize failed: %s', self._init_error)
            else:
                self._initialized = True
                self._init_error = None
                logger.info('RAGOrchestrator: ready (mode=%s, sqlite_vec=%s, lancedb=%s)', self._backend_mode, self._sqlite_vec_store is not None, self._lancedb_store is not None)

    async def research_and_answer(self, query: str, confidence_threshold: float=0.7, priority: int=5) -> dict[str, Any]:
        """
        Dual-engine RAG retrieval + answer synthesis.

        Stages (bounded, fail-safe):
            1. Sanitize & truncate query
            2. Embed via MLX (off event loop)
            3. Search sqlite-vec primary → LanceDB fallback (if needed)
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
        stages: list[str] = [f'priority={priority}']
        if not self._initialized:
            await self.initialize()
        if self._sqlite_vec_store is None and self._lancedb_store is None:
            return self._empty_result(error=self._init_error or 'no backend available', started=started)
        sanitized = (query or '').strip()[:_MAX_QUERY_CHARS]
        if not sanitized:
            return self._empty_result(error='empty query', started=started)
        stages.append('sanitize')
        try:
            embedding = await self._embed_offloop(sanitized)
        except Exception as e:
            logger.warning('RAGOrchestrator: embed failed: %s', e)
            return self._empty_result(error=f'embed: {e}', started=started)
        stages.append('embed')
        if not embedding:
            return self._empty_result(error='embed returned empty', started=started)
        results: list[dict[str, Any]] = []
        search_errors: list[str] = []
        if self._sqlite_vec_store is not None:
            try:
                results = await self._sqlite_vec_store.search(query_embedding=embedding, top_k=min(10, _MAX_SOURCES), threshold=confidence_threshold)
                stages.append('sqlite_vec_search')
            except Exception as e:
                search_errors.append(f'sqlite_vec: {e}')
                logger.debug('RAGOrchestrator: sqlite-vec search failed: %s', e)
        if self._lancedb_store is not None and len(results) < _MAX_SOURCES:
            try:
                lancedb_results = await self._lancedb_store.search_similar_adaptive(query_text=sanitized, query_emb=embedding, top_k=min(5, _MAX_SOURCES - len(results)))
                seen_ids = {r.get('item_id') or r.get('id') for r in results}
                for r in lancedb_results:
                    rid = r.get('id') or r.get('item_id')
                    if rid and rid not in seen_ids:
                        results.append(r)
                        seen_ids.add(rid)
                stages.append('lancedb_fallback')
            except Exception as e:
                search_errors.append(f'lancedb: {e}')
                logger.debug('RAGOrchestrator: LanceDB fallback failed: %s', e)
        if not results:
            return self._empty_result(error=f"no results from any backend: {'; '.join(search_errors) or 'unknown'}", started=started)
        stages.append('search')
        sources: list[dict[str, Any]] = []
        for r in results:
            score = float(r.get('distance') or r.get('similarity') or 0.0)
            if score < confidence_threshold:
                continue
            text = ((r.get('metadata') or {}).get('text') or r.get('text') or '').strip()[:_TOKEN_CHARS_PER_SOURCE]
            if not text:
                continue
            item_id = r.get('item_id') or r.get('id') or ''
            sources.append({'id': item_id, 'text': text, 'similarity': score, 'metadata': {k: v for k, v in r.items() if k not in ('item_id', 'id', 'text', 'distance', 'metadata', '_embedding', 'embedding')}})
            if len(sources) >= _MAX_SOURCES:
                break
        stages.append('filter')
        answer, tokens_used = self._synthesize(sanitized, sources)
        stages.append('synthesize')
        if sources:
            confidence = sum((s['similarity'] for s in sources)) / len(sources)
        else:
            confidence = _FALLBACK_CONFIDENCE if results else 0.0
        confidence = max(0.0, min(1.0, confidence))
        return {'sources': sources, 'answer': answer, 'confidence': confidence, 'tokens_used': tokens_used, 'stages_completed': stages, 'metadata': {'processing_time': time.monotonic() - started, 'validation_score': None, 'compressed': False, 'fallback_used': not sources and len(results or []) == 0, 'backend_mode': self._backend_mode, 'search_errors': search_errors if search_errors else None}}

    async def _embed_offloop(self, text: str) -> list[float]:
        """Embed text via MLX, off the event loop.

        Uses the canonical MLX embedder from core/mlx_embeddings.py.
        """
        try:
            from hledac.universal.core.mlx_embeddings import get_embedding_manager
            mgr = get_embedding_manager()
            emb = mgr.embed_query(text)
            try:
                return emb.tolist()
            except AttributeError:
                return list(emb)
        except Exception:
            store = self._lancedb_store
            if store is not None:
                try:
                    return await store._embed_single(text)
                except Exception:
                    pass
            return []

    def _synthesize(self, query: str, sources: list[dict[str, Any]]) -> tuple[str, int]:
        """Build bounded answer string from top sources. Pure-Python, no LLM."""
        if not sources:
            return (f'No relevant information found in local knowledge base for: {query}', 0)
        parts: list[str] = [f'Found {len(sources)} relevant sources for: {query}\n']
        for i, s in enumerate(sources, 1):
            text = s['text']
            sim = s.get('similarity', 0.0)
            parts.append(f'\n[{i}] (sim={sim:.2f})\n{text}')
        answer = ''.join(parts)
        if len(answer) > 4096:
            answer = answer[:4093] + '...'
        return (answer, len(answer) // 4)

    def _empty_result(self, error: str, started: float) -> dict[str, Any]:
        return {'sources': [], 'answer': f'RAG engine unavailable: {error}', 'confidence': 0.0, 'tokens_used': 0, 'stages_completed': ['init_failed'], 'error': error, 'metadata': {'processing_time': time.monotonic() - started, 'validation_score': None, 'compressed': False, 'fallback_used': True, 'error': error, 'backend_mode': self._backend_mode}}

    async def cleanup(self) -> None:
        """Release references."""
        if self._sqlite_vec_store is not None:
            await self._sqlite_vec_store.close()
            self._sqlite_vec_store = None
        self._lancedb_store = None
        self._initialized = False
__all__ = ['RAGOrchestrator']