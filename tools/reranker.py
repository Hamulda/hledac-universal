"""
Lightweight Reranking Engine using FlashRank
=========================================

Memory-efficient reranking solution using FlashRank
with TinyBERT-L-2 model (~4MB), optimized for M1 MacBook Air (8GB RAM).

FlashRank uses quantized ONNX models for maximum inference speed
and minimal memory footprint.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# Konstanty pro reranking
MAX_RERANK_DOCS = 50

try:
    from flashrank import Ranker, RerankRequest as FlashRankRequest
    FLASHRANK_AVAILABLE = True
except ImportError:
    FLASHRANK_AVAILABLE = False
    logger.warning("FlashRank not installed. Install with: pip install flashrank")


@dataclass
class RerankResult:
    """Result from reranking operation."""
    document_id: str
    content: str
    original_score: float
    reranked_score: float
    score_delta: float
    rank: int


@dataclass
class RerankRequest:
    """Request for reranking."""
    query: str
    documents: List[Dict[str, Any]]
    top_k: Optional[int] = None
    return_all: bool = False


class LightweightReranker:
    """
    Memory-efficient reranker using FlashRank with TinyBERT-L-2.
    
    Model: ms-marco-MiniLM-L-12-v2 (~4MB)
    Backend: ONNX Runtime (quantized)
    Purpose: Reorder search results by relevance to query
    
    Advantages:
    - ~4MB vs ~500MB for CrossEncoder
    - ONNX Runtime for M1 optimization
    - Instant loading, no cnew start penalty
    - Low memory footprint (~20MB peak)
    """
    
    def __init__(self, model_name: str = "ms-marco-MiniLM-L-12-v2", cache_dir: Optional[str] = None):
        """
        Initialize lightweight reranker.
        
        Args:
            model_name: FlashRank model name (default: TinyBERT-L-2-v2)
            cache_dir: Optional cache directory for models
        """
        self.model_name = model_name
        self.cache_dir = cache_dir or "/tmp"
        self.ranker: Optional[Ranker] = None
        self.is_loaded = False
        
        if not FLASHRANK_AVAILABLE:
            logger.error("FlashRank not available. Install with: pip install flashrank")
            return
        
        self._initialize_ranker()
    
    def _initialize_ranker(self):
        """Initialize FlashRank ranker with minimal memory usage."""
        try:
            logger.info(f"Initializing FlashRank reranker: {self.model_name}")
            
            self.ranker = Ranker(
                model_name=self.model_name,
                cache_dir=self.cache_dir,
                max_length=512
            )
            
            self.is_loaded = True
            logger.info("FlashRank reranker loaded (model: ~4MB)")
            
        except Exception as e:
            logger.error(f"Failed to initialize FlashRank: {e}")
            self.ranker = None
            self.is_loaded = False
    
    async def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: Optional[int] = None,
        return_all: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Rerank documents based on query relevance.

        Vstup: list dictů, každý musí mít klíče 'idx' (int) a 'content' (str).
        Vrací nový list dictů (kopie) s přidanými klíči 'reranked_score' a 'rank'.
        Při chybě vrací původní documents (bez reranked_score/rank).
        """
        if not documents:
            return documents

        if len(documents) > MAX_RERANK_DOCS:
            logger.debug(f"Truncating input from {len(documents)} to {MAX_RERANK_DOCS}")
            documents = documents[:MAX_RERANK_DOCS]

        # Zajistíme, že každý dokument má 'content' (jinak použijeme prázdný řetězec)
        cleaned_docs = []
        for d in documents:
            new_d = d.copy()
            if 'content' not in new_d:
                new_d['content'] = ''
            cleaned_docs.append(new_d)

        try:
            if self.is_loaded:
                results = await asyncio.get_running_loop().run_in_executor(
                    None, self._rerank_sync, query, cleaned_docs, top_k, return_all
                )
                # Konverze RerankResult na Dict
                return [
                    {
                        'idx': r.document_id,
                        'content': r.content,
                        'reranked_score': r.reranked_score,
                        'rank': r.rank,
                        'original_score': r.original_score
                    }
                    for r in results
                ]
            else:
                return self._fallback_rerank(query, cleaned_docs, top_k)

        except Exception as e:
            logger.warning(f"Reranking failed, returning original order: {e}")
            return documents
    
    def _rerank_sync(
        self, 
        query: str, 
        documents: List[Dict[str, Any]], 
        top_k: Optional[int],
        return_all: bool
    ) -> List[RerankResult]:
        """Synchronous reranking using FlashRank."""
        
        request = FlashRankRequest(
            query=query,
            passages=[
                {"id": str(i), "text": doc.get("content", doc.get("text", ""))}
                for i, doc in enumerate(documents)
            ]
        )
        
        rank_results = self.ranker.rerank(request)
        
        reranked_results = []
        for rank, result in enumerate(rank_results):
            doc_idx = int(result["id"])
            original_doc = documents[doc_idx]
            
            rerank_result = RerankResult(
                document_id=original_doc.get("id", str(doc_idx)),
                content=original_doc.get("content", original_doc.get("text", "")),
                original_score=original_doc.get("score", 0.0),
                reranked_score=result.get("score", 0.0),
                score_delta=result.get("score", 0.0) - original_doc.get("score", 0.0),
                rank=rank + 1
            )
            
            reranked_results.append(rerank_result)
        
        if top_k and top_k < len(reranked_results):
            reranked_results = reranked_results[:top_k]
        
        return reranked_results
    
    def _fallback_rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: Optional[int]
    ) -> List[Dict[str, Any]]:
        """Keyword matching – deterministický, stabilní řazení."""
        logger.debug("Using fallback keyword-based reranking")

        query_words = set(query.lower().split())
        if not query_words:
            # Prázdný dotaz – vrátíme dokumenty beze změny (s rank 1..N)
            output = []
            for i, d in enumerate(documents):
                new_d = d.copy()
                new_d['reranked_score'] = 0.0
                new_d['rank'] = i + 1
                output.append(new_d)
            return output

        scored = []
        for d in documents:
            words = set(d['content'].lower().split())
            overlap = len(query_words & words)
            score = overlap / len(query_words)  # podíl překryvu
            scored.append((score, d))

        # Stabilní řazení (při rovnosti zachová původní pořadí)
        scored.sort(key=lambda x: x[0], reverse=True)

        output = []
        for rank, (score, d) in enumerate(scored):
            new_d = d.copy()
            new_d['reranked_score'] = score
            new_d['rank'] = rank + 1
            output.append(new_d)
            if top_k and len(output) >= top_k:
                break

        return output
    
    async def batch_rerank(
        self, 
        requests: List[RerankRequest]
    ) -> List[List[RerankResult]]:
        """
        Batch reranking for multiple queries.
        
        Args:
            requests: List of reranking requests
            
        Returns:
            List of reranked results for each request
        """
        tasks = [
            self.rerank(req.query, req.documents, req.top_k, req.return_all)
            for req in requests
        ]
        
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """Get estimated memory usage."""
        return {
            "model_name": self.model_name,
            "model_size_mb": 4.0,
            "is_loaded": self.is_loaded,
            "backend": "ONNX Runtime",
            "quantization": "int8"
        }
    
    def unload(self):
        """Unload reranker and free memory."""
        if self.ranker:
            del self.ranker
            self.ranker = None
            self.is_loaded = False
            
            logger.info("FlashRank reranker unloaded")
            
            import gc
            gc.collect()


class RerankerFactory:
    """Factory for creating rerankers."""
    
    @staticmethod
    def create_lightweight_reranker(
        model_name: str = "ms-marco-MiniLM-L-12-v2",
        cache_dir: Optional[str] = None
    ) -> LightweightReranker:
        """Create a lightweight reranker instance."""
        return LightweightReranker(model_name, cache_dir)
    
    @staticmethod
    def create_fallback_reranker() -> LightweightReranker:
        """Create a fallback keyword-based reranker."""
        return LightweightReranker(model_name="fallback", cache_dir=None)


class RerankerConfig:
    """Configuration for reranker."""
    
    def __init__(
        self,
        model_name: str = "ms-marco-MiniLM-L-12-v2",
        cache_dir: Optional[str] = None,
        max_length: int = 512,
        enable_cache: bool = True,
        cache_size: int = 1000
    ):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.max_length = max_length
        self.enable_cache = enable_cache
        self.cache_size = cache_size


def create_reranker(config: Optional[RerankerConfig] = None) -> LightweightReranker:
    """
    Convenience function to create reranker.

    Args:
        config: Optional reranker configuration

    Returns:
        Initialized LightweightReranker instance
    """
    if config is None:
        config = RerankerConfig()

    return RerankerFactory.create_lightweight_reranker(
        model_name=config.model_name,
        cache_dir=config.cache_dir
    )


# ---------------------------------------------------------------------------
# Diagnostic helpers
# ---------------------------------------------------------------------------

def get_reranker_backend() -> str:
    """Return the current reranker backend/model name.

    This is a read-only diagnostic - does not load or initialize anything.
    """
    return "FlashRank / ms-marco-MiniLM-L-12-v2"


def get_reranker_status() -> dict:
    """Return reranker status diagnostics.

    Read-only diagnostic - does not load any models.

    Returns:
        dict with backend, model, and status info
    """
    return {
        "backend": "FlashRank",
        "model": "ms-marco-MiniLM-L-12-v2",
        "model_size_mb": 4,
        "canonical_owner": "tools/reranker.py",
        "future_candidates": {
            "bge_reranker": "BAAI/bge-reranker-v2-m3 (DEFERRED)",
            "jina_reranker": "jinaai/jina-reranker-v2-base (DEFERRED)",
            "mem_reranker": "MemGPT/MemReranker-0.6B (DEFERRED)"
        },
        "note": "bge/jina/MemReranker require benchmark evidence before activation"
    }
