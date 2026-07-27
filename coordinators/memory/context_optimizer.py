"""
Context Optimization Manager
===========================

Context optimization with three-tier storage (hot/warm/cold) and compression.

Extracted from memory_coordinator.py (F320) — original line range: 989-1269

Features:
- Three-tier storage: hot (RAM), warm (cache), cold (disk)
- FastEmbed embeddings for semantic search (optional)
- LZ4 compression for storage
- Phase-based prioritization

Canonical import:
    from coordinators.memory import ContextOptimizationManager
"""

import logging
import time
from dataclasses import dataclass, field
import msgspec
from enum import Enum
from pathlib import Path
from typing import Any

from hledac.universal.utils.msgspec_json import encode_zstd as _encode_zstd
from hledac.universal.utils.msgspec_json import decode_zstd as _decode_zstd

logger = logging.getLogger(__name__)


class ContextPriority(Enum):
    """Priority levels for context items."""
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'


class ResearchPhase(Enum):
    """Research phases for context prioritization."""
    DATA_COLLECTION = 'data_collection'
    ANALYSIS = 'analysis'
    SYNTHESIS = 'synthesis'
    VALIDATION = 'validation'


class ContextItem(msgspec.Struct, gc=False):
    """Individual context item with metadata for three-tier storage."""
    item_id: str
    content: str
    metadata: dict[str, Any]
    tokens: int
    priority: ContextPriority
    access_count: int
    last_accessed: float
    embedding: Any | None = None
    content_type: str = 'general'
    confidence: float = 0.5


class CompressedContext(msgspec.Struct, gc=False):
    """Compressed context container."""
    context_id: str
    original_size: int
    compressed_size: int
    compression_ratio: float
    critical_content: str
    important_summary: str
    abstract_summary: str
    full_compressed: bytes
    metadata: dict[str, Any]
    timestamp: float


class ContextOptimizationManager:
    """
    Context optimization with three-tier storage and compression.

    Three-tier storage: hot (RAM), warm (cache), cold (disk).
    """
    __slots__ = tuple((
        'cold_storage', 'embedder', 'embedding_dim', 'enable_embeddings',
        'hot_context', 'hot_tokens', 'max_hot_tokens', 'max_warm_tokens',
        'phase_weights', 'stats', 'storage_path', 'warm_context', 'warm_tokens'
    ))

    def __init__(
        self,
        max_hot_tokens: int = 20000,
        max_warm_tokens: int = 40000,
        storage_path: str = './context_cache',
        enable_embeddings: bool = False,
    ) -> None:
        """
        Initialize context optimization manager.

        Args:
            max_hot_tokens: Maximum tokens in hot (RAM) storage
            max_warm_tokens: Maximum tokens in warm (cache) storage
            storage_path: Path for persistent storage
            enable_embeddings: Whether to enable semantic embeddings
        """
        self.max_hot_tokens = max_hot_tokens
        self.max_warm_tokens = max_warm_tokens
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.hot_context: dict[str, ContextItem] = {}
        self.warm_context: dict[str, ContextItem] = {}
        self.cold_storage: dict[str, ContextItem] = {}
        self.hot_tokens = 0
        self.warm_tokens = 0
        self.enable_embeddings = enable_embeddings
        self.embedder = None
        self.embedding_dim = 384
        if enable_embeddings:
            self._initialize_embedder()
        self.stats = {
            'hits': 0, 'misses': 0, 'evictions': 0,
            'promotions': 0, 'compressions': 0, 'total_requests': 0
        }
        self.phase_weights = {
            ResearchPhase.DATA_COLLECTION: {'data_source': 0.9, 'research': 0.7},
            ResearchPhase.ANALYSIS: {'analysis': 0.9, 'insight': 0.8},
            ResearchPhase.SYNTHESIS: {'synthesis': 0.9, 'summary': 0.8},
            ResearchPhase.VALIDATION: {'validation': 0.9, 'evidence': 0.7},
        }
        logger.info(
            f'ContextOptimizationManager initialized (hot: {max_hot_tokens}, warm: {max_warm_tokens})'
        )

    def _serialize_to_json(self, data: Any) -> bytes:
        """Serialize data to JSON bytes using msgspec, compressed with zstd."""
        return _encode_zstd(data)

    def _initialize_embedder(self) -> None:
        """Initialize MLXEmbedder (primary) — Apple Silicon native, M1 8GB optimal."""
        try:
            from hledac.universal.brain.mlx_embedder import MLXEmbedder
            self.embedder = MLXEmbedder()
            self._mlx_embedder = self.embedder
            self.embedding_dim = 384
            logger.info('MLXEmbedder initialized for semantic search')
        except Exception:
            logger.warning('MLXEmbedder not available, semantic search disabled')
            self.enable_embeddings = False

    def add_context(
        self,
        item_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        priority: ContextPriority = ContextPriority.MEDIUM,
        phase: ResearchPhase = ResearchPhase.DATA_COLLECTION,
    ) -> bool:
        """
        Add context item to three-tier storage.

        Args:
            item_id: Unique item identifier
            content: Content to store
            metadata: Additional metadata
            priority: Item priority
            phase: Current research phase

        Returns:
            True if added successfully
        """
        metadata = metadata or {}
        tokens = len(content.split())
        content_type = metadata.get('type', 'general')
        phase_weight = self.phase_weights.get(phase, {}).get(content_type, 0.5)
        item = ContextItem(
            item_id=item_id,
            content=content,
            metadata=metadata,
            tokens=tokens,
            priority=priority,
            access_count=0,
            last_accessed=time.time(),
            content_type=content_type,
            confidence=metadata.get('confidence', 0.5),
        )
        if priority == ContextPriority.HIGH or phase_weight > 0.8:
            if self.hot_tokens + tokens > self.max_hot_tokens:
                self._evict_from_hot(tokens)
            self.hot_context[item_id] = item
            self.hot_tokens += tokens
        elif priority == ContextPriority.MEDIUM or phase_weight > 0.5:
            if self.warm_tokens + tokens > self.max_warm_tokens:
                self._evict_from_warm(tokens)
            self.warm_context[item_id] = item
            self.warm_tokens += tokens
        else:
            self.cold_storage[item_id] = item
            self._persist_to_disk(item)
        return True

    def get_context(self, item_id: str) -> str | None:
        """
        Retrieve context item with automatic promotion.

        Args:
            item_id: Item identifier

        Returns:
            Content if found, None otherwise
        """
        self.stats['total_requests'] += 1
        if item_id in self.hot_context:
            item = self.hot_context[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self.stats['hits'] += 1
            return item.content
        if item_id in self.warm_context:
            item = self.warm_context[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self._promote_to_hot(item)
            self.stats['hits'] += 1
            return item.content
        if item_id in self.cold_storage:
            item = self.cold_storage[item_id]
            item.access_count += 1
            item.last_accessed = time.time()
            self._promote_to_warm(item)
            self.stats['hits'] += 1
            return item.content
        self.stats['misses'] += 1
        return None

    def compress_context(
        self,
        context_id: str,
        content: str,
        compression_level: int = 3,
    ) -> CompressedContext:
        """
        Compress context using LZ4.

        Args:
            context_id: Unique identifier
            content: Content to compress
            compression_level: LZ4 compression level

        Returns:
            CompressedContext object
        """
        try:
            import lz4.frame
            original_size = len(content.encode('utf-8'))
            compressed = lz4.frame.compress(
                content.encode('utf-8'),
                compression_level=compression_level,
            )
            compressed_size = len(compressed)
            words = content.split()
            critical = ' '.join(words[:50]) if len(words) > 50 else content
            important = ' '.join(words[:100]) if len(words) > 100 else content
            abstract = ' '.join(words[:20]) if len(words) > 20 else content
            result = CompressedContext(
                context_id=context_id,
                original_size=original_size,
                compressed_size=compressed_size,
                compression_ratio=original_size / max(compressed_size, 1),
                critical_content=critical,
                important_summary=important,
                abstract_summary=abstract,
                full_compressed=compressed,
                metadata={'compression_level': compression_level},
                timestamp=time.time(),
            )
            self.stats['compressions'] += 1
            return result
        except ImportError:
            logger.warning('LZ4 not available, returning uncompressed')
            return CompressedContext(
                context_id=context_id,
                original_size=len(content.encode('utf-8')),
                compressed_size=len(content.encode('utf-8')),
                compression_ratio=1.0,
                critical_content=content[:200],
                important_summary=content[:500],
                abstract_summary=content[:100],
                full_compressed=content.encode('utf-8'),
                metadata={},
                timestamp=time.time(),
            )

    def decompress_context(
        self,
        compressed: CompressedContext,
        detail_level: str = 'important',
    ) -> str:
        """
        Decompress context at specified detail level.

        Args:
            compressed: CompressedContext object
            detail_level: 'critical', 'important', or 'abstract'

        Returns:
            Decompressed content
        """
        if detail_level == 'critical':
            return compressed.critical_content
        elif detail_level == 'abstract':
            return compressed.abstract_summary
        else:
            try:
                import lz4.frame
                return lz4.frame.decompress(compressed.full_compressed).decode('utf-8')
            except Exception:
                return compressed.important_summary

    def _evict_from_hot(self, required_tokens: int) -> None:
        """Evict items from hot storage to make room."""
        items = sorted(
            self.hot_context.items(),
            key=lambda x: (x[1].priority.value, x[1].last_accessed),
        )
        freed = 0
        for item_id, item in items:
            if freed >= required_tokens:
                break
            del self.hot_context[item_id]
            self.hot_tokens -= item.tokens
            freed += item.tokens
            if self.warm_tokens + item.tokens <= self.max_warm_tokens:
                self.warm_context[item_id] = item
                self.warm_tokens += item.tokens
            else:
                self._evict_from_warm(item.tokens)
                self.warm_context[item_id] = item
                self.warm_tokens += item.tokens
        self.stats['evictions'] += 1

    def _evict_from_warm(self, required_tokens: int) -> None:
        """Evict items from warm storage to cold storage."""
        items = sorted(
            self.warm_context.items(),
            key=lambda x: (x[1].priority.value, x[1].last_accessed),
        )
        freed = 0
        for item_id, item in items:
            if freed >= required_tokens:
                break
            del self.warm_context[item_id]
            self.warm_tokens -= item.tokens
            freed += item.tokens
            self.cold_storage[item_id] = item
            self._persist_to_disk(item)

    def _promote_to_hot(self, item: ContextItem) -> None:
        """Promote item from warm to hot storage."""
        if item.tokens > self.max_hot_tokens:
            return
        if self.hot_tokens + item.tokens > self.max_hot_tokens:
            self._evict_from_hot(item.tokens)
        if item.item_id in self.warm_context:
            del self.warm_context[item.item_id]
            self.warm_tokens -= item.tokens
        self.hot_context[item.item_id] = item
        self.hot_tokens += item.tokens
        self.stats['promotions'] += 1

    def _promote_to_warm(self, item: ContextItem) -> None:
        """Promote item from cold to warm storage."""
        if item.tokens > self.max_warm_tokens:
            return
        if self.warm_tokens + item.tokens > self.max_warm_tokens:
            self._evict_from_warm(item.tokens)
        if item.item_id in self.cold_storage:
            del self.cold_storage[item.item_id]
        self.warm_context[item.item_id] = item
        self.warm_tokens += item.tokens
        self.stats['promotions'] += 1

    def _persist_to_disk(self, item: ContextItem) -> None:
        """Persist item to disk storage."""
        file_path = self.storage_path / f'{item.item_id}.json'
        try:
            with open(file_path, 'wb') as f:
                f.write(self._serialize_to_json(item))
        except Exception as e:
            logger.error(f'Failed to persist {item.item_id}: {e}')

    def get_stats(self) -> dict[str, Any]:
        """Get context optimization statistics."""
        return {
            **self.stats,
            'hot_items': len(self.hot_context),
            'warm_items': len(self.warm_context),
            'cold_items': len(self.cold_storage),
            'hot_tokens': self.hot_tokens,
            'warm_tokens': self.warm_tokens,
            'hit_rate': self.stats['hits'] / max(self.stats['total_requests'], 1),
        }
