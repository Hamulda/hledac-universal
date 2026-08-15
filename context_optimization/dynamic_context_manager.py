"""
Dynamic Context Manager with MLX Embeddings (M1-primary)
=============================================




MLXEmbeddingManager is primary for M1. FastEmbed removed P0-1.

This module provides memory-efficient context management using MLX embeddings
with Metal backend, optimized for M1 MacBook Air (8GB RAM).
"""
import hashlib
import logging
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum

from hledac.universal.compat.msgspec_gc_compat import Struct
from pathlib import Path
from typing import TYPE_CHECKING, Any
import numpy as np
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode
from core import aclose
if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)
FASTEMBED_AVAILABLE = False
try:
    from hledac.universal.core.mlx_embeddings import MLXEmbeddingManager
    MLX_EMBED_AVAILABLE = True
except ImportError:
    MLX_EMBED_AVAILABLE = False
    logger.debug('MLXEmbeddingManager not available')

class Priority(Enum):
    """Priority levels for context items."""
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'
    AUTO = 'auto'

class ResearchPhase(Enum):
    """Research phases for context prioritization."""
    DATA_COLLECTION = 'data_collection'
    ANALYSIS = 'analysis'
    SYNTHESIS = 'synthesis'
    VALIDATION = 'validation'

def _deserialize_context_item(data: dict[str, Any]) -> ContextItem:
    """Deserialize a ContextItem from dict."""
    return ContextItem(item_id=data['item_id'], content=data['content'], metadata=data['metadata'], tokens=data['tokens'], priority=Priority(data['priority']) if isinstance(data['priority'], str) else data['priority'], access_count=data['access_count'], last_accessed=data['last_accessed'], embedding=np.array(data['embedding']) if data.get('embedding') is not None else None, content_type=data.get('content_type', 'general'), confidence=data.get('confidence', 0.5), phase_relevance=data.get('phase_relevance'))

def _serialize_cnew(data: dict[str, ContextItem]) -> bytes:
    """Serialize cnew storage to bytes using orjson."""
    serializable = {}
    for k, v in data.items():
        entry_dict = {'item_id': v.item_id, 'content': v.content, 'metadata': v.metadata, 'tokens': v.tokens, 'priority': v.priority.value if isinstance(v.priority, Enum) else v.priority, 'access_count': v.access_count, 'last_accessed': v.last_accessed, 'embedding': v.embedding.tolist() if v.embedding is not None else None, 'content_type': v.content_type, 'confidence': v.confidence, 'phase_relevance': v.phase_relevance}
        serializable[k] = entry_dict
    return _msgspec_encode(serializable)

def _deserialize_cnew(data: bytes) -> dict[str, ContextItem]:
    """Deserialize cnew storage from bytes using msgspec facade."""
    raw = _msgspec_decode(data)
    result = {}
    for k, v in raw.items():
        result[k] = _deserialize_context_item(v)
    return result

class ContextItem(Struct):
    """Individual context item with metadata."""
    item_id: str
    content: str
    metadata: dict[str, Any]
    tokens: int
    priority: Priority
    access_count: int
    last_accessed: float
    embedding: np.ndarray | None = None
    content_type: str = 'general'
    confidence: float = 0.5
    phase_relevance: dict[str, float] = None

class ContextStats(Struct):
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
    __slots__ = tuple(('_embedder_type', '_mlx_manager', '_semantic_index', 'access_log', 'cnew_storage', 'cnew_storage_file', 'current_phase', 'current_query', 'embedder', 'embedding_dim', 'embedding_model', 'embedding_to_id', 'hot_context', 'hot_tokens', 'max_hot_tokens', 'max_warm_tokens', 'phase_weights', 'stats', 'storage_path', 'warm_context', 'warm_tokens'))

    def __init__(self, max_hot_tokens: int=20000, max_warm_tokens: int=40000, embedding_model: str='snowflake/snowflake-arctic-embed-xs', storage_path: str='./context_cache'):
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
        self.hot_context: dict[str, ContextItem] = {}
        self.warm_context: dict[str, ContextItem] = {}
        self.cnew_storage: dict[str, ContextItem] = {}
        self.hot_tokens = 0
        self.warm_tokens = 0
        self.embedder = None
        self.embedding_dim = None
        self._embedder_type = None
        if MLX_EMBED_AVAILABLE:
            try:
                from hledac.universal.core.mlx_embeddings import get_embedding_manager
                self._mlx_manager = get_embedding_manager()
                self.embedder = self._mlx_manager
                self.embedding_dim = self._mlx_manager.EMBEDDING_DIM
                self._embedder_type = 'mlx'
                logger.info(f'[EMBEDDER] Using shared MLXEmbeddingManager: {self._mlx_manager.model_path}, dim={self.embedding_dim}')
            except Exception as e:
                logger.warning(f'MLXEmbeddingManager init failed: {e}, using dummy embeddings')
                self._mlx_manager = None
                self.embedder = None
                self.embedding_dim = 384
                self._embedder_type = None
        elif FASTEMBED_AVAILABLE:
            self._initialize_embedder()
        else:
            logger.warning('MLXEmbeddingManager not available, using dummy embeddings')
            self.embedding_dim = 384
        self._semantic_index = None
        self.embedding_to_id: dict[int, str] = {}
        self.access_log: dict[str, int] = {}
        self.current_query: str | None = None
        self.current_phase: ResearchPhase = ResearchPhase.DATA_COLLECTION
        self.stats: dict[str, Any] = {'hits': 0, 'misses': 0, 'evictions': 0, 'promotions': 0, 'total_requests': 0}
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.cnew_storage_file = self.storage_path / 'cnew_storage.json'
        self._load_cnew_storage()
        self.phase_weights = {ResearchPhase.DATA_COLLECTION: {'general': 0.8, 'data_source': 0.9, 'research': 0.7}, ResearchPhase.ANALYSIS: {'analysis': 0.9, 'insight': 0.8, 'data': 0.6}, ResearchPhase.SYNTHESIS: {'synthesis': 0.9, 'summary': 0.8, 'conclusion': 0.8}, ResearchPhase.VALIDATION: {'validation': 0.9, 'verification': 0.8, 'evidence': 0.7}}

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

    def _get_embeddings(self, texts: list[str]) -> list[np.ndarray]:
        """Get embeddings for texts (uses query task for retrieval)."""
        if self.embedder is None:
            return []
        try:
            if self._embedder_type == 'mlx':
                if hasattr(self.embedder, 'embed_query'):
                    return [self.embedder.embed_query(t) for t in texts]
                results = self.embedder.encode(texts)
                return [np.asarray(r.tolist()) if hasattr(r, 'tolist') else np.array(r) for r in results]
            else:
                return list(self.embedder.embed(texts))
        except Exception as e:
            logger.warning(f'Embedding failed: {e}')
            return []

    def _load_cnew_storage(self):
        """Load cnew storage from disk if available."""
        try:
            if self.cnew_storage_file.exists():
                with open(self.cnew_storage_file, 'rb') as f:
                    self.cnew_storage = _deserialize_cnew(f.read())
                logger.info(f'Loaded {len(self.cnew_storage)} items from cnew storage')
        except FileNotFoundError:
            self.cnew_storage = {}
        except Exception as e:
            logger.warning(f'Could not load cnew storage: {e}')
            self.cnew_storage = {}

    def _save_cnew_storage(self):
        """Save cnew storage to disk."""
        try:
            with open(self.cnew_storage_file, 'wb') as f:
                f.write(_serialize_cnew(self.cnew_storage))
        except Exception as e:
            logger.warning(f'Could not save cnew storage: {e}')

    def _generate_item_id(self, content: str) -> str:
        """Generate unique ID for content item."""
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count (rough approximation: 1 token ≈ 4 characters)."""
        return len(text) // 4

    async def add_item(self, content: str, metadata: dict[str, Any] | None=None) -> str:
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
        item_id = self._generate_item_id(content)
        if item_id in self.hot_context or item_id in self.warm_context:
            return item_id
        tokens = self._estimate_tokens(content)
        context_item = ContextItem(item_id=item_id, content=content, metadata=metadata, tokens=tokens, priority=Priority.AUTO, access_count=0, last_accessed=time.time(), content_type=metadata.get('content_type', 'general'), confidence=metadata.get('confidence', 0.5))
        if self.embedder:
            embeddings = self._get_embeddings([content])
            if embeddings:
                embedding = np.array(embeddings[0])
                context_item.embedding = embedding
        if context_item.priority == Priority.AUTO:
            context_item.priority = self._calculate_priority(content, metadata)
        self._add_to_tier(context_item)
        self._check_eviction()
        return item_id

    def _calculate_priority(self, content: str, metadata: dict[str, Any]) -> Priority:
        """Calculate priority for a context item."""
        scores = {}
        timestamp = metadata.get('timestamp', time.time())
        time_diff = time.time() - timestamp
        recency_score = max(0.1, 1.0 - time_diff / 3600)
        scores['recency'] = recency_score
        if self.current_query and self.embedder:
            content_embeddings = self._get_embeddings([content])
            query_embeddings = self._get_embeddings([self.current_query])
            if content_embeddings and query_embeddings:
                content_embedding = np.array(content_embeddings[0])
                query_embedding = np.array(query_embeddings[0])
                similarity = float(np.dot(content_embedding, query_embedding) / (np.linalg.norm(content_embedding) * np.linalg.norm(query_embedding)))
                relevance_score = max(0.1, similarity)
            else:
                relevance_score = 0.5
        else:
            relevance_score = 0.5
        scores['relevance'] = relevance_score
        content_type = metadata.get('content_type', 'general')
        phase_weight = self.phase_weights.get(self.current_phase, {}).get(content_type, 0.5)
        scores['phase'] = phase_weight
        confidence_score = metadata.get('confidence', 0.5)
        scores['confidence'] = confidence_score
        frequency_score = min(1.0, metadata.get('access_count', 0) / 10.0)
        scores['frequency'] = frequency_score
        weights = {'relevance': 0.4, 'phase': 0.3, 'recency': 0.15, 'confidence': 0.1, 'frequency': 0.05}
        total_score = sum((scores[k] * weights[k] for k in scores))
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
        if self.hot_tokens > self.max_hot_tokens:
            victims = self._find_eviction_victims(self.hot_context, 0.2)
            for victim_id in victims:
                victim_item = self.hot_context.pop(victim_id)
                self.hot_tokens -= victim_item.tokens
                self.stats['evictions'] += 1
                self._add_to_warm(victim_item)
        if self.warm_tokens > self.max_warm_tokens:
            victims = self._find_eviction_victims(self.warm_context, 0.3)
            for victim_id in victims:
                victim_item = self.warm_context.pop(victim_id)
                self.warm_tokens -= victim_item.tokens
                self.stats['evictions'] += 1
                self._add_to_cnew(victim_item)

    def _find_eviction_victims(self, context: dict[str, ContextItem], fraction: float) -> list[str]:
        """Find items to evict based on priority and access time."""
        priority_order = {Priority.LOW: 0, Priority.MEDIUM: 1, Priority.HIGH: 2}
        items = list(context.items())
        items.sort(key=lambda x: (priority_order[x[1].priority], x[1].last_accessed))
        victim_count = max(1, int(len(items) * fraction))
        return [item_id for item_id, _ in items[:victim_count]]

    async def get_item(self, item_id: str) -> ContextItem | None:
        """
        Get an item from context.

        Args:
            item_id: ID of the item to retrieve

        Returns:
            ContextItem if found, None otherwise
        """
        self.stats['total_requests'] += 1
        if item_id in self.hot_context:
            item = self.hot_context[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self.access_log[item_id] = self.access_log.get(item_id, 0) + 1
            self.stats['hits'] += 1
            return item
        if item_id in self.warm_context:
            item = self.warm_context.pop(item_id)
            self.warm_tokens -= item.tokens
            item.access_count += 1
            item.last_accessed = time.time()
            self.stats['hits'] += 1
            self.stats['promotions'] += 1
            self._add_to_hot(item)
            return item
        if item_id in self.cnew_storage:
            item = self.cnew_storage[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self.stats['hits'] += 1
            self.stats['promotions'] += 1
            self._add_to_warm(item)
            return item
        self.stats['misses'] += 1
        return None

    async def search(self, query: str, top_k: int=10) -> list[tuple[str, float]]:
        """
        Search context for relevant items.

        Args:
            query: Search query
            top_k: Number of results to return

        Returns:
            List of (item_id, similarity_score) tuples
        """
        self.current_query = query
        if self.embedder:
            query_embeddings = self._get_embeddings([query])
            if query_embeddings:
                query_embedding = np.array(query_embeddings[0]).reshape(1, -1)
            else:
                return []
        else:
            return []
        distances, indices = self.semantic_index.search(query_embedding, top_k)
        results = []
        for idx, similarity in zip(indices[0], distances[0], strict=False):
            item_id = self.embedding_to_id.get(int(idx))
            results.append((item_id, float(similarity)))
        return results

    def set_phase(self, phase: ResearchPhase):
        """Set current research phase."""
        self.current_phase = phase

    def _rebalance_context(self):
        """Rebalance context based on current research phase."""
        all_items = []
        all_items.extend(list(self.hot_context.values()))
        all_items.extend(list(self.warm_context.values()))
        for item in all_items:
            content_type = item.metadata.get('content_type', 'general')
            phase_weight = self.phase_weights.get(self.current_phase, {}).get(content_type, 0.5)
            if phase_weight > 0.7:
                item.priority = Priority.HIGH
                if item.item_id in self.warm_context:
                    self.warm_context.pop(item.item_id)
                    self.warm_tokens -= item.tokens
                    self._add_to_hot(item)
                elif item.item_id in self.cnew_storage:
                    self.cnew_storage.pop(item.item_id)
                    self._add_to_warm(item)
            elif phase_weight < 0.3:
                item.priority = Priority.LOW
                if item.item_id in self.hot_context:
                    self.hot_context.pop(item.item_id)
                    self.hot_tokens -= item.tokens
                    self._add_to_warm(item)

    async def get_formatted_context(self, max_tokens: int | None=None) -> str:
        """
        Get formatted context string for LLM.

        Args:
            max_tokens: Maximum tokens to include (None = use hot tier)

        Returns:
            Formatted context string
        """
        if max_tokens is None:
            max_tokens = self.max_hot_tokens
        context_items = list(self.hot_context.values())
        context_items.sort(key=lambda x: (x.last_accessed, x.access_count), reverse=True)
        formatted_parts = []
        current_tokens = 0
        for item in context_items:
            if current_tokens + item.tokens > max_tokens:
                break
            formatted_parts.append(f'[{item.content_type.upper()}] {item.content}')
            current_tokens += item.tokens
        return '\n\n'.join(formatted_parts)

    def get_stats(self) -> ContextStats:
        """Get comprehensive context management statistics."""
        hit_rate = self.stats['hits'] / max(1, self.stats['total_requests'])
        total_memory = 0
        all_items = list(self.hot_context.values()) + list(self.warm_context.values())
        for item in all_items:
            total_memory += sys.getsizeof(item)
        total_memory_mb = total_memory / (1024 * 1024)
        return ContextStats(hot_items=len(self.hot_context), warm_items=len(self.warm_context), cnew_items=len(self.cnew_storage), hot_tokens=self.hot_tokens, warm_tokens=self.warm_tokens, total_memory_mb=total_memory_mb, hit_rate=hit_rate, eviction_count=self.stats['evictions'], promotion_count=self.stats['promotions'])

    def clear_all(self):
        """Clear all context storage."""
        self.hot_context.clear()
        self.warm_context.clear()
        self.cnew_storage.clear()
        self.hot_tokens = 0
        self.warm_tokens = 0
        self.access_log.clear()
        import faiss
        self._semantic_index = faiss.IndexFlatIP(self.embedding_dim)
        self.embedding_to_id.clear()
        if self.cnew_storage_file.exists():
            self.cnew_storage_file.unlink()
        for key in self.stats:
            self.stats[key] = 0

    @property
    def total_items(self) -> int:
        """Total number of items across all tiers."""
        return len(self.hot_context) + len(self.warm_context) + len(self.cnew_storage)

    def __repr__(self) -> str:
        """String representation of context manager state."""
        stats = self.get_stats()
        return f'DynamicContextManager(hot={stats.hot_items}, warm={stats.warm_items}, cnew={stats.cnew_items}, hit_rate={stats.hit_rate:.2f})'