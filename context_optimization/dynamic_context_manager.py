"""
Dynamic Context Manager with FastEmbed (ONNX)
==========================================

OPTIMIZED: PyTorch backend removed in favor of ONNX Runtime via FastEmbed

This module provides memory-efficient context management using FastEmbed
with ONNX runtime, optimized for M1 MacBook Air (8GB RAM).

FastEmbed uses quantized ONNX models for maximum inference speed
and minimal memory footprint (~50MB vs ~420MB for PyTorch).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False
    import json as _json

if TYPE_CHECKING:
    import faiss

logger = logging.getLogger(__name__)

try:
    from fastembed import TextEmbedding
    FASTEMBED_AVAILABLE = True
except ImportError:
    FASTEMBED_AVAILABLE = False
    logger.warning("FastEmbed not installed. Install with: pip install fastembed")

# MLX Embedding Manager (primary for M1)
try:
    from hledac.core.mlx_embeddings import MLXEmbeddingManager
    MLX_EMBED_AVAILABLE = True
except ImportError:
    MLX_EMBED_AVAILABLE = False
    logger.debug("MLXEmbeddingManager not available")


class Priority(Enum):
    """Priority levels for context items."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    AUTO = "auto"


class ResearchPhase(Enum):
    """Research phases for context prioritization."""
    DATA_COLLECTION = "data_collection"
    ANALYSIS = "analysis"
    SYNTHESIS = "synthesis"
    VALIDATION = "validation"


def _ndarray_to_list(obj: Any) -> Any:
    """Convert numpy arrays to lists for JSON serialization."""
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if is_dataclass(obj):
        return {k: _ndarray_to_list(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _ndarray_to_list(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ndarray_to_list(item) for item in obj]
    return obj


def _deserialize_context_item(data: Dict[str, Any]) -> ContextItem:
    """Deserialize a ContextItem from dict."""
    return ContextItem(
        item_id=data['item_id'],
        content=data['content'],
        metadata=data['metadata'],
        tokens=data['tokens'],
        priority=Priority(data['priority']) if isinstance(data['priority'], str) else data['priority'],
        access_count=data['access_count'],
        last_accessed=data['last_accessed'],
        embedding=np.array(data['embedding']) if data.get('embedding') is not None else None,
        content_type=data.get('content_type', 'general'),
        confidence=data.get('confidence', 0.5),
        phase_relevance=data.get('phase_relevance'),
    )


def _serialize_cnew(data: Dict[str, ContextItem]) -> bytes:
    """Serialize cnew storage to bytes using orjson."""
    serializable = {}
    for k, v in data.items():
        entry_dict = {
            'item_id': v.item_id,
            'content': v.content,
            'metadata': v.metadata,
            'tokens': v.tokens,
            'priority': v.priority.value if isinstance(v.priority, Enum) else v.priority,
            'access_count': v.access_count,
            'last_accessed': v.last_accessed,
            'embedding': v.embedding.tolist() if v.embedding is not None else None,
            'content_type': v.content_type,
            'confidence': v.confidence,
            'phase_relevance': v.phase_relevance,
        }
        serializable[k] = entry_dict
    if ORJSON_AVAILABLE:
        return orjson.dumps(serializable)
    return _json.dumps(serializable).encode()


def _deserialize_cnew(data: bytes) -> Dict[str, ContextItem]:
    """Deserialize cnew storage from bytes using orjson."""
    if ORJSON_AVAILABLE:
        raw = orjson.loads(data)
    else:
        raw = _json.loads(data.decode())

    result = {}
    for k, v in raw.items():
        result[k] = _deserialize_context_item(v)
    return result


@dataclass
class ContextItem:
    """Individual context item with metadata."""
    item_id: str
    content: str
    metadata: Dict[str, Any]
    tokens: int
    priority: Priority
    access_count: int
    last_accessed: float
    embedding: Optional[np.ndarray] = None
    content_type: str = "general"
    confidence: float = 0.5
    phase_relevance: Dict[str, float] = None


@dataclass
class ContextStats:
    """Context management statistics."""
    hot_items: int
    warm_items: int
    cnew_items: int
    hot_tokens: int
    warm_tokens: int
    total_memory_mb: float
    hit_rate: float
    eviction_count: int
    promotion_count: int


class DynamicContextManager:
    """
    Three-tier context manager with FastEmbed (ONNX) backend.
    
    Model: BAAI/bge-small-en-v1.5 or snowflake/snowflake-arctic-embed-xs (~50-130MB)
    Backend: ONNX Runtime (quantized)
    Purpose: Intelligent context management with semantic similarity
    
    Advantages:
    - ~50MB vs ~420MB for PyTorch-based all-mpnet-base-v2
    - ONNX Runtime for M1 optimization
    - Instant loading, minimal cnew start penalty
    - Low memory footprint (~100MB peak)
    """
    
    def __init__(
        self,
        max_hot_tokens: int = 20_000,
        max_warm_tokens: int = 40_000,
        embedding_model: str = "snowflake/snowflake-arctic-embed-xs",
        storage_path: str = "./context_cache"
    ):
        """
        Initialize dynamic context manager.
        
        Args:
            max_hot_tokens: Maximum tokens in hot storage
            max_warm_tokens: Maximum tokens in warm storage
            embedding_model: FastEmbed model name
            storage_path: Path for persistent storage
        """
        self.max_hot_tokens = max_hot_tokens
        self.max_warm_tokens = max_warm_tokens
        self.embedding_model = embedding_model
        
        # Three-tier storage
        self.hot_context: Dict[str, ContextItem] = {}
        self.warm_context: Dict[str, ContextItem] = {}
        self.cnew_storage: Dict[str, ContextItem] = {}
        
        # Token tracking
        self.hot_tokens = 0
        self.warm_tokens = 0
        
        # Embedding model for semantic similarity
        self.embedder = None
        self.embedding_dim = None
        self._embedder_type = None

        # Initialize MLX Embedding Manager (primary for M1) or fallback to FastEmbed
        # Use shared singleton to avoid duplicate model loads
        if MLX_EMBED_AVAILABLE:
            try:
                from hledac.core.mlx_embeddings import get_embedding_manager
                self._mlx_manager = get_embedding_manager()
                self.embedder = self._mlx_manager
                self.embedding_dim = self._mlx_manager.EMBEDDING_DIM
                self._embedder_type = 'mlx'
                logger.info(f"[EMBEDDER] Using shared MLXEmbeddingManager: {self._mlx_manager.model_path}, dim={self.embedding_dim}")
            except Exception as e:
                logger.warning(f"MLXEmbeddingManager init failed: {e}, falling back to FastEmbed")
                self._mlx_manager = None
                if FASTEMBED_AVAILABLE:
                    self._initialize_embedder()
                else:
                    logger.warning("FastEmbed not available, using dummy embeddings")
                    self.embedding_dim = 384
        elif FASTEMBED_AVAILABLE:
            self._initialize_embedder()
        else:
            logger.warning("FastEmbed not available, using dummy embeddings")
            self.embedding_dim = 384
        
        # FAISS index for semantic search - lazy loaded
        self._semantic_index = None
        self.embedding_to_id: Dict[int, str] = {}

    @property
    def semantic_index(self):
        """Lazy-loaded FAISS semantic index."""
        if self._semantic_index is None:
            import faiss
            self._semantic_index = faiss.IndexFlatIP(self.embedding_dim)
        return self._semantic_index

    def _ensure_faiss(self):
        """Ensure faiss is imported before use."""
        if self._semantic_index is None:
            import faiss
            self._semantic_index = faiss.IndexFlatIP(self.embedding_dim)
        
        # Access tracking
        self.access_log: Dict[str, int] = {}
        self.current_query: Optional[str] = None
        self.current_phase: ResearchPhase = ResearchPhase.DATA_COLLECTION
        
        # Statistics
        self.stats: Dict[str, Any] = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'promotions': 0,
            'total_requests': 0
        }
        
        # Storage configuration
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.cnew_storage_file = self.storage_path / "cnew_storage.json"
        
        # Load existing cnew storage if available
        self._load_cnew_storage()
        
        # Phase-specific weights for prioritization
        self.phase_weights = {
            ResearchPhase.DATA_COLLECTION: {
                'general': 0.8,
                'data_source': 0.9,
                'research': 0.7
            },
            ResearchPhase.ANALYSIS: {
                'analysis': 0.9,
                'insight': 0.8,
                'data': 0.6
            },
            ResearchPhase.SYNTHESIS: {
                'synthesis': 0.9,
                'summary': 0.8,
                'conclusion': 0.8
            },
            ResearchPhase.VALIDATION: {
                'validation': 0.9,
                'verification': 0.8,
                'evidence': 0.7
            }
        }
    
    def _initialize_embedder(self):
        """Initialize FastEmbed embedder with minimal memory usage."""
        try:
            logger.info(f"Initializing FastEmbed embedder: {self.embedding_model}")

            self.embedder = TextEmbedding(
                model_name=self.embedding_model,
                cache_dir=str(self.storage_path / "embeddings"),
                threads=4  # Optimize for M1
            )

            self.embedding_dim = self.embedder.embedding_size
            self._embedder_type = 'fastembed'
            logger.info(f"✅ FastEmbed embedder loaded (model: ~50MB, dim: {self.embedding_dim})")

        except Exception as e:
            logger.error(f"Failed to initialize FastEmbed: {e}")
            self.embedder = None
            self.embedding_dim = 384
            self._embedder_type = None

    def _get_embeddings(self, texts: List[str]) -> List[np.ndarray]:
        """Get embeddings for texts (uses query task for retrieval)."""
        if self.embedder is None:
            return []

        try:
            if self._embedder_type == 'mlx':
                # Sprint 87: Use embed_query() for retrieval (SEARCH_QUERY task)
                if hasattr(self.embedder, 'embed_query'):
                    return [self.embedder.embed_query(t) for t in texts]
                results = self.embedder.encode(texts)
                return [np.array(r.tolist() if hasattr(r, 'tolist') else r) for r in results]
            else:
                # FastEmbed uses .embed()
                return list(self.embedder.embed(texts))
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return []

    def _load_cnew_storage(self):
        """Load cnew storage from disk if available."""
        try:
            if self.cnew_storage_file.exists():
                with open(self.cnew_storage_file, 'rb') as f:
                    self.cnew_storage = _deserialize_cnew(f.read())
                logger.info(f"Loaded {len(self.cnew_storage)} items from cnew storage")
        except FileNotFoundError:
            self.cnew_storage = {}
        except Exception as e:
            logger.warning(f"Could not load cnew storage: {e}")
            self.cnew_storage = {}

    def _save_cnew_storage(self):
        """Save cnew storage to disk."""
        try:
            with open(self.cnew_storage_file, 'wb') as f:
                f.write(_serialize_cnew(self.cnew_storage))
        except Exception as e:
            logger.warning(f"Could not save cnew storage: {e}")
    
    def _generate_item_id(self, content: str) -> str:
        """Generate unique ID for content item."""
        return hashlib.md5(content.encode()).hexdigest()[:16]
    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count (rough approximation: 1 token ≈ 4 characters)."""
        return len(text) // 4
    
    async def add_item(
        self,
        content: str,
        metadata: Dict[str, Any] = None
    ) -> str:
        """
        Add an item to the context.
        
        Args:
            content: Text content to add
            metadata: Optional metadata dictionary
            
        Returns:
            Item ID
        """
        if metadata is None:
            metadata = {}
        
        # Generate item ID
        item_id = self._generate_item_id(content)
        
        # Check if item already exists
        if item_id in self.hot_context or item_id in self.warm_context:
            return item_id
        
        # Estimate tokens
        tokens = self._estimate_tokens(content)
        
        # Create context item
        context_item = ContextItem(
            item_id=item_id,
            content=content,
            metadata=metadata,
            tokens=tokens,
            priority=Priority.AUTO,
            access_count=0,
            last_accessed=time.time(),
            content_type=metadata.get('content_type', 'general'),
            confidence=metadata.get('confidence', 0.5)
        )
        
        # Generate embedding
        if self.embedder:
            embeddings = self._get_embeddings([content])
            if embeddings:
                embedding = np.array(embeddings[0])
                context_item.embedding = embedding
        
        # Determine priority if auto
        if context_item.priority == Priority.AUTO:
            context_item.priority = self._calculate_priority(content, metadata)
        
        # Add to appropriate tier
        self._add_to_tier(context_item)
        
        # Check for eviction if needed
        self._check_eviction()
        
        return item_id
    
    def _calculate_priority(
        self,
        content: str,
        metadata: Dict[str, Any]
    ) -> Priority:
        """Calculate priority for a context item."""
        scores = {}
        
        # Recency score
        timestamp = metadata.get('timestamp', time.time())
        time_diff = time.time() - timestamp
        recency_score = max(0.1, 1.0 - (time_diff / 3600))
        scores['recency'] = recency_score
        
        # Relevance score (semantic similarity to current query)
        if self.current_query and self.embedder:
            content_embeddings = self._get_embeddings([content])
            query_embeddings = self._get_embeddings([self.current_query])
            
            if content_embeddings and query_embeddings:
                content_embedding = np.array(content_embeddings[0])
                query_embedding = np.array(query_embeddings[0])
                
                similarity = float(np.dot(content_embedding, query_embedding) / (
                    np.linalg.norm(content_embedding) * np.linalg.norm(query_embedding)
                ))
                relevance_score = max(0.1, similarity)
            else:
                relevance_score = 0.5
        else:
            relevance_score = 0.5
        
        scores['relevance'] = relevance_score
        
        # Phase relevance score
        content_type = metadata.get('content_type', 'general')
        phase_weight = self.phase_weights.get(self.current_phase, {}).get(content_type, 0.5)
        scores['phase'] = phase_weight
        
        # Confidence score
        confidence_score = metadata.get('confidence', 0.5)
        scores['confidence'] = confidence_score
        
        # Frequency score (access count if exists)
        frequency_score = min(1.0, metadata.get('access_count', 0) / 10.0)
        scores['frequency'] = frequency_score
        
        # Weighted combination
        weights = {
            'relevance': 0.4,
            'phase': 0.3,
            'recency': 0.15,
            'confidence': 0.1,
            'frequency': 0.05
        }
        
        total_score = sum(scores[k] * weights[k] for k in scores)
        
        # Convert to priority level
        if total_score > 0.7:
            return Priority.HIGH
        elif total_score > 0.4:
            return Priority.MEDIUM
        else:
            return Priority.LOW
    
    def _add_to_tier(self, item: ContextItem):
        """Add item to appropriate tier based on priority."""
        if item.priority == Priority.HIGH:
            self._add_to_hot(item)
        elif item.priority == Priority.MEDIUM:
            self._add_to_warm(item)
        else:
            self._add_to_cnew(item)
    
    def _add_to_hot(self, item: ContextItem):
        """Add item to hot context."""
        self.hot_context[item.item_id] = item
        self.hot_tokens += item.tokens
        self.access_log[item.item_id] = 1
        
        # Add to semantic index
        if item.embedding is not None:
            embedding_id = len(self.embedding_to_id)
            self.embedding_to_id[embedding_id] = item.item_id
            self.semantic_index.add(item.embedding.reshape(1, -1).astype('float32'))
    
    def _add_to_warm(self, item: ContextItem):
        """Add item to warm context."""
        self.warm_context[item.item_id] = item
        self.warm_tokens += item.tokens
        self.access_log[item.item_id] = 1
    
    def _add_to_cnew(self, item: ContextItem):
        """Add item to cnew storage."""
        self.cnew_storage[item.item_id] = item
        self._save_cnew_storage()
    
    def _check_eviction(self):
        """Check and perform eviction if tiers are over capacity."""
        # Evict from hot to warm if needed
        if self.hot_tokens > self.max_hot_tokens:
            victims = self._find_eviction_victims(self.hot_context, 0.2)
            for victim_id in victims:
                victim_item = self.hot_context.pop(victim_id)
                self.hot_tokens -= victim_item.tokens
                self.stats['evictions'] += 1
                self._add_to_warm(victim_item)
        
        # Evict from warm to cnew if needed
        if self.warm_tokens > self.max_warm_tokens:
            victims = self._find_eviction_victims(self.warm_context, 0.3)
            for victim_id in victims:
                victim_item = self.warm_context.pop(victim_id)
                self.warm_tokens -= victim_item.tokens
                self.stats['evictions'] += 1
                self._add_to_cnew(victim_item)
    
    def _find_eviction_victims(
        self,
        context: Dict[str, ContextItem],
        fraction: float
    ) -> List[str]:
        """Find items to evict based on priority and access time."""
        # Sort by priority and access time
        priority_order = {Priority.LOW: 0, Priority.MEDIUM: 1, Priority.HIGH: 2}
        items = list(context.items())
        items.sort(key=lambda x: (priority_order[x[1].priority], x[1].last_accessed))
        
        victim_count = max(1, int(len(items) * fraction))
        return [item_id for item_id, _ in items[:victim_count]]
    
    async def get_item(self, item_id: str) -> Optional[ContextItem]:
        """
        Get an item from context.
        
        Args:
            item_id: ID of the item to retrieve
            
        Returns:
            ContextItem if found, None otherwise
        """
        self.stats['total_requests'] += 1
        
        # Check hot context
        if item_id in self.hot_context:
            item = self.hot_context[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self.access_log[item_id] = self.access_log.get(item_id, 0) + 1
            self.stats['hits'] += 1
            return item
        
        # Check warm context and promote to hot
        if item_id in self.warm_context:
            item = self.warm_context.pop(item_id)
            self.warm_tokens -= item.tokens
            item.access_count += 1
            item.last_accessed = time.time()
            self.stats['hits'] += 1
            self.stats['promotions'] += 1
            self._add_to_hot(item)
            return item
        
        # Check cnew storage and promote to warm
        if item_id in self.cnew_storage:
            item = self.cnew_storage[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self.stats['hits'] += 1
            self.stats['promotions'] += 1
            self._add_to_warm(item)
            return item
        
        # Item not found
        self.stats['misses'] += 1
        return None
    
    async def search(
        self,
        query: str,
        top_k: int = 10
    ) -> List[Tuple[str, float]]:
        """
        Search context for relevant items.
        
        Args:
            query: Search query
            top_k: Number of results to return
            
        Returns:
            List of (item_id, similarity_score) tuples
        """
        self.current_query = query
        
        # Encode query
        if self.embedder:
            query_embeddings = self._get_embeddings([query])
            if query_embeddings:
                query_embedding = np.array(query_embeddings[0]).reshape(1, -1)
            else:
                return []
        else:
            return []
        
        # Search in semantic index
        D, I = self.semantic_index.search(query_embedding, top_k)
        
        results = []
        for idx, similarity in zip(I[0], D[0]):
            item_id = self.embedding_to_id.get(int(idx))
            results.append((item_id, float(similarity)))
        
        return results
    
    def set_phase(self, phase: ResearchPhase):
        """Set current research phase."""
        self.current_phase = phase
    
    def _rebalance_context(self):
        """Rebalance context based on current research phase."""
        # Get all items
        all_items = []
        all_items.extend(list(self.hot_context.values()))
        all_items.extend(list(self.warm_context.values()))
        
        # Re-prioritize based on current phase
        for item in all_items:
            content_type = item.metadata.get('content_type', 'general')
            phase_weight = self.phase_weights.get(self.current_phase, {}).get(content_type, 0.5)
            
            # Update phase relevance
            if phase_weight > 0.7:
                item.priority = Priority.HIGH
                # Promote to hot if not already there
                if item.item_id in self.warm_context:
                    self.warm_context.pop(item.item_id)
                    self.warm_tokens -= item.tokens
                    self._add_to_hot(item)
                elif item.item_id in self.cnew_storage:
                    self.cnew_storage.pop(item.item_id)
                    self._add_to_warm(item)
            elif phase_weight < 0.3:
                item.priority = Priority.LOW
                # Demote to warm if not relevant to current phase
                if item.item_id in self.hot_context:
                    self.hot_context.pop(item.item_id)
                    self.hot_tokens -= item.tokens
                    self._add_to_warm(item)
    
    async def get_formatted_context(
        self,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Get formatted context string for LLM.
        
        Args:
            max_tokens: Maximum tokens to include (None = use hot tier)
            
        Returns:
            Formatted context string
        """
        if max_tokens is None:
            max_tokens = self.max_hot_tokens
        
        # Sort by relevance and recency
        context_items = list(self.hot_context.values())
        context_items.sort(key=lambda x: (x.last_accessed, x.access_count), reverse=True)
        
        # Build formatted context
        formatted_parts = []
        current_tokens = 0
        
        for item in context_items:
            if current_tokens + item.tokens > max_tokens:
                break
            
            formatted_parts.append(f"[{item.content_type.upper()}] {item.content}")
            current_tokens += item.tokens
        
        return "\n\n".join(formatted_parts)
    
    def get_stats(self) -> ContextStats:
        """Get comprehensive context management statistics."""
        hit_rate = self.stats['hits'] / max(1, self.stats['total_requests'])

        # Estimate memory usage
        total_memory = 0
        all_items = list(self.hot_context.values()) + list(self.warm_context.values())
        for item in all_items:
            total_memory += sys.getsizeof(item)

        total_memory_mb = total_memory / (1024 * 1024)
        
        return ContextStats(
            hot_items=len(self.hot_context),
            warm_items=len(self.warm_context),
            cnew_items=len(self.cnew_storage),
            hot_tokens=self.hot_tokens,
            warm_tokens=self.warm_tokens,
            total_memory_mb=total_memory_mb,
            hit_rate=hit_rate,
            eviction_count=self.stats['evictions'],
            promotion_count=self.stats['promotions']
        )
    
    def clear_all(self):
        """Clear all context storage."""
        self.hot_context.clear()
        self.warm_context.clear()
        self.cnew_storage.clear()
        self.hot_tokens = 0
        self.warm_tokens = 0
        self.access_log.clear()
        
        # Reset semantic index
        import faiss
        self._semantic_index = faiss.IndexFlatIP(self.embedding_dim)
        self.embedding_to_id.clear()
        
        # Delete cnew storage file
        if self.cnew_storage_file.exists():
            self.cnew_storage_file.unlink()
        
        # Reset stats
        for key in self.stats:
            self.stats[key] = 0
    
    @property
    def total_items(self) -> int:
        """Total number of items across all tiers."""
        return len(self.hot_context) + len(self.warm_context) + len(self.cnew_storage)
    
    def __repr__(self) -> str:
        """String representation of context manager state."""
        stats = self.get_stats()
        return (f"DynamicContextManager(hot={stats.hot_items}, "
                f"warm={stats.warm_items}, cnew={stats.cnew_items}, "
                f"hit_rate={stats.hit_rate:.2f})")
