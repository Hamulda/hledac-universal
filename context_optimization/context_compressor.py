"""
Context Compressor with FastEmbed (ONNX)
====================================

OPTIMIZED: PyTorch backend removed in favor of ONNX Runtime via FastEmbed

This module provides memory-efficient context compression using FastEmbed
with ONNX runtime, optimized for M1 MacBook Air (8GB RAM).

FastEmbed uses quantized ONNX models for maximum inference speed
and minimal memory footprint (~50MB vs ~420MB for PyTorch).
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

import lz4.frame
import numpy as np

try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False
    import json as _json

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

CRITICAL = "critical"
IMPORTANT = "important"
ABSTRACT = "abstract"


def _ndarray_to_list(obj: Any) -> Any:
    """Convert numpy arrays to lists for JSON serialization."""
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, bytes):
        # Skip bytes (full_compressed is LZ4 compressed bytes - keep as base64)
        import base64
        return base64.b64encode(obj).decode('ascii')
    if is_dataclass(obj):
        return {k: _ndarray_to_list(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _ndarray_to_list(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ndarray_to_list(item) for item in obj]
    return obj


def _list_to_ndarray(obj: Any) -> Any:
    """Convert lists back to numpy arrays after JSON deserialization."""
    if isinstance(obj, str) and len(obj) > 100:
        # Likely base64 encoded bytes
        import base64
        return base64.b64decode(obj)
    if isinstance(obj, dict):
        return {k: _list_to_ndarray(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_list_to_ndarray(item) for item in obj]
    return obj


def _serialize_compressed(data: Dict[str, CompressedContext]) -> bytes:
    """Serialize compressed context data to bytes using orjson."""
    serializable = {}
    for k, v in data.items():
        entry_dict = {
            'context_id': v.context_id,
            'original_size': v.original_size,
            'compressed_size': v.compressed_size,
            'compression_ratio': v.compression_ratio,
            'critical_content': v.critical_content,
            'important_summary': v.important_summary,
            'abstract_summary': v.abstract_summary,
            'full_compressed': v.full_compressed,  # bytes
            'metadata': v.metadata,
            'timestamp': v.timestamp,
            'embeddings': {kk: vv.tolist() for kk, vv in v.embeddings.items()} if v.embeddings else None,
            'sentence_scores': v.sentence_scores,
            'cluster_info': v.cluster_info,
        }
        serializable[k] = entry_dict
    if ORJSON_AVAILABLE:
        return orjson.dumps(serializable)
    return _json.dumps(serializable).encode()


def _deserialize_compressed(data: bytes) -> Dict[str, CompressedContext]:
    """Deserialize compressed context data from bytes using orjson."""
    if ORJSON_AVAILABLE:
        raw = orjson.loads(data)
    else:
        raw = _json.loads(data.decode())

    result = {}
    for k, v in raw.items():
        result[k] = CompressedContext(
            context_id=v['context_id'],
            original_size=v['original_size'],
            compressed_size=v['compressed_size'],
            compression_ratio=v['compression_ratio'],
            critical_content=v['critical_content'],
            important_summary=v['important_summary'],
            abstract_summary=v['abstract_summary'],
            full_compressed=v['full_compressed'],  # bytes
            metadata=v['metadata'],
            timestamp=v['timestamp'],
            embeddings={kk: np.array(vv) for kk, vv in v['embeddings'].items()} if v['embeddings'] else None,
            sentence_scores=v['sentence_scores'],
            cluster_info=v['cluster_info'],
        )
    return result


class CompressionLevel(Enum):
    """Compression levels for context."""
    CRITICAL = CRITICAL
    IMPORTANT = IMPORTANT
    ABSTRACT = ABSTRACT


@dataclass
class CompressedContext:
    """Compressed context container."""
    context_id: str
    original_size: int
    compressed_size: int
    compression_ratio: float
    critical_content: str
    important_summary: str
    abstract_summary: str
    full_compressed: bytes
    metadata: Dict[str, Any]
    timestamp: float
    embeddings: Optional[Dict[str, np.ndarray]] = None
    sentence_scores: Optional[List[float]] = None
    cluster_info: Optional[Dict[str, Any]] = None


@dataclass
class DecompressionResult:
    """Result of context decompression."""
    content: str
    detail_level: str
    relevance_score: float
    decompression_time: float
    source_level: CompressionLevel


class ContextCompressor:
    """
    Context compressor with FastEmbed (ONNX) backend.
    
    Model: BAAI/bge-small-en-v1.5 or snowflake/snowflake-arctic-embed-xs (~50-130MB)
    Backend: ONNX Runtime (quantized)
    Purpose: Multi-level context compression with semantic analysis
    
    Advantages:
    - ~50MB vs ~420MB for PyTorch-based all-mpnet-base-v2
    - ONNX Runtime for M1 optimization
    - Instant loading, minimal cnew start penalty
    - Low memory footprint (~100MB peak)
    - LZ4 compression for storage
    """
    
    def __init__(
        self,
        embedding_model: str = "snowflake/snowflake-arctic-embed-xs",
        storage_path: str = "compressed_storage",
        max_critical_tokens: int = 10_000,
        max_important_tokens: int = 20_000,
        max_abstract_tokens: int = 5_000
    ):
        """
        Initialize context compressor.
        
        Args:
            embedding_model: FastEmbed model name
            storage_path: Path for compressed storage
            max_critical_tokens: Max tokens for critical content
            max_important_tokens: Max tokens for important summary
            max_abstract_tokens: Max tokens for abstract summary
        """
        # Initialize models
        self.embedding_model = embedding_model
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
        
        # Storage
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.compressed_storage: Dict[str, CompressedContext] = {}
        
        # Configuration
        self.max_critical_tokens = max_critical_tokens
        self.max_important_tokens = max_important_tokens
        self.max_abstract_tokens = max_abstract_tokens
        
        # Performance metrics
        self.compression_stats: Dict[str, Any] = {
            "total_compressed": 0,
            "total_original_tokens": 0,
            "total_compressed_tokens": 0,
            "compression_time": 0.0,
            "decompression_time": 0.0
        }
        
        # Load existing compressed contexts
        self._load_compressed_storage()
    
    def _initialize_embedder(self):
        """Initialize FastEmbed embedder with minimal memory usage."""
        try:
            logger.info(f"Initializing FastEmbed embedder: {self.embedding_model}")

            self.embedder = TextEmbedding(
                model_name=self.embedding_model,
                cache_dir=str(self.storage_path / "embeddings"),
                threads=4
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
        """Get embeddings for texts (uses query task for retrieval/similarity)."""
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
                return list(self.embedder.embed(texts))
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return []
    
    def _load_compressed_storage(self):
        """Load existing compressed contexts from disk."""
        try:
            storage_file = self.storage_path / "compressed_contexts.json"
            if storage_file.exists():
                with open(storage_file, 'rb') as f:
                    self.compressed_storage = _deserialize_compressed(f.read())
                logger.info(f"Loaded {len(self.compressed_storage)} compressed contexts")
        except FileNotFoundError:
            self.compressed_storage = {}
        except Exception as e:
            logger.warning(f"Could not load compressed storage: {e}")
            self.compressed_storage = {}

    def _save_compressed_storage(self):
        """Save compressed contexts to disk."""
        try:
            storage_file = self.storage_path / "compressed_contexts.json"
            with open(storage_file, 'wb') as f:
                f.write(_serialize_compressed(self.compressed_storage))
        except Exception as e:
            logger.warning(f"Could not save compressed storage: {e}")
    
    def _generate_context_id(self, content: str) -> str:
        """Generate unique ID for content."""
        return hashlib.md5(content.encode()).hexdigest()[:16]
    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count (approximate)."""
        return len(text) // 4
    
    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        sentences = re.split(r'[.!?]+', text)
        return [s.strip() for s in sentences if s.strip()]
    
    def _chunk_text(self, text: str, chunk_size: int = 1024) -> List[str]:
        """Split text into chunks for processing."""
        words = text.split()
        chunks = []
        current_chunk = []
        current_length = 0
        
        for word in words:
            if current_length + len(word) + 1 > chunk_size:
                chunks.append(' '.join(current_chunk))
                current_chunk = [word]
                current_length = len(word)
            else:
                current_chunk.append(word)
                current_length += len(word) + 1
        
        if current_chunk:
            chunks.append(' '.join(current_chunk))
        
        return chunks
    
    async def compress_context(
        self,
        full_context: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> CompressedContext:
        """
        Compress context with multi-level compression.
        
        Args:
            full_context: Full context to compress
            metadata: Optional metadata
            
        Returns:
            CompressedContext object
        """
        start_time = time.time()
        if metadata is None:
            metadata = {}
        
        # Generate context ID
        context_id = self._generate_context_id(full_context)
        
        # Check if already compressed
        if context_id in self.compressed_storage:
            return self.compressed_storage[context_id]
        
        original_tokens = self._estimate_tokens(full_context)
        
        # Level 1: Extract critical information (verbatim)
        critical_content = self._extract_critical_content(
            full_context, 
            self.max_critical_tokens
        )
        
        # Level 2: Summarize important information
        important_summary = self._create_summary(
            full_context, 
            self.max_important_tokens
        )
        
        # Level 3: Create high-level abstract
        abstract_summary = self._create_abstract(
            full_context, 
            self.max_abstract_tokens
        )
        
        # Compress full context with LZ4
        full_compressed = lz4.frame.compress(full_context.encode('utf-8'))
        
        # Calculate compression ratio
        compressed_tokens = (
            self._estimate_tokens(critical_content) +
            self._estimate_tokens(important_summary) +
            self._estimate_tokens(abstract_summary)
        )
        compression_ratio = compressed_tokens / max(original_tokens, 1)
        
        # Create compressed context object
        compressed_ctx = CompressedContext(
            context_id=context_id,
            original_size=original_tokens,
            compressed_size=compressed_tokens,
            compression_ratio=compression_ratio,
            critical_content=critical_content,
            important_summary=important_summary,
            abstract_summary=abstract_summary,
            full_compressed=full_compressed,
            metadata=metadata,
            timestamp=time.time()
        )
        
        # Store compressed context
        self.compressed_storage[context_id] = compressed_ctx
        self._save_compressed_storage()
        
        # Update statistics
        compression_time = time.time() - start_time
        self.compression_stats["total_compressed"] += 1
        self.compression_stats["total_original_tokens"] += original_tokens
        self.compression_stats["total_compressed_tokens"] += compressed_tokens
        self.compression_stats["compression_time"] += compression_time
        
        return compressed_ctx
    
    def _extract_critical_content(
        self, 
        content: str, 
        max_tokens: int
    ) -> str:
        """Extract most critical sentences using embedding-based scoring."""
        sentences = self._split_sentences(content)
        
        if not sentences:
            return ""
        
        # Score sentences based on multiple factors
        scored_sentences = []
        for idx, sentence in enumerate(sentences):
            score = self._score_sentence(sentence, idx, len(sentences))
            scored_sentences.append((sentence, score))
        
        # Sort by score and select top sentences
        scored_sentences.sort(key=lambda x: x[1], reverse=True)
        
        selected_sentences = []
        current_tokens = 0
        
        for sentence, score in scored_sentences:
            sentence_tokens = self._estimate_tokens(sentence)
            if current_tokens + sentence_tokens > max_tokens:
                break
            selected_sentences.append(sentence)
            current_tokens += sentence_tokens
        
        return ' '.join(selected_sentences)
    
    def _score_sentence(
        self, 
        sentence: str, 
        position: int, 
        total_sentences: int
    ) -> float:
        """Score sentence based on multiple factors."""
        score = 0.0
        
        # Length score (medium-length sentences often more informative)
        length = len(sentence.split())
        if 10 <= length <= 30:
            score += 0.3
        elif 5 <= length <= 50:
            score += 0.2
        else:
            score += 0.1
        
        # Position score (beginning and end often important)
        total_sentences = max(total_sentences - 1, 1)
        position_ratio = position / total_sentences
        if position_ratio < 0.2 or position_ratio > 0.8:
            score += 0.3
        elif position_ratio < 0.4 or position_ratio > 0.6:
            score += 0.2
        
        # Keyword indicators
        sentence_lower = sentence.lower()
        if any(kw in sentence_lower for kw in ['important', 'critical', 'key', 'main', 'primary']):
            score += 0.1
        
        # Numerical data presence (often important)
        if re.search(r'\d+', sentence):
            score += 0.1
        
        # Question or citation presence
        if '?' in sentence or '"' in sentence:
            score += 0.1
        
        return score
    
    def _create_summary(
        self, 
        content: str, 
        max_tokens: int
    ) -> str:
        """Create summary of content."""
        # Split content into manageable chunks
        chunks = self._chunk_text(content, chunk_size=1024)
        
        summaries = []
        
        # Generate summary for each chunk
        for chunk in chunks:
            # Simple extractive summarization
            sentences = self._split_sentences(chunk)
            chunk_summary = sentences[:5]  # Top 5 sentences
            summaries.append(' '.join(chunk_summary))
        
        # Combine summaries and ensure length constraint
        combined_summary = ' '.join(summaries)
        
        # Truncate if still too long
        sentences = self._split_sentences(combined_summary)
        result_sentences = []
        current_tokens = 0
        
        for sentence in sentences:
            tokens = self._estimate_tokens(sentence)
            if current_tokens + tokens > max_tokens:
                break
            result_sentences.append(sentence)
            current_tokens += tokens
        
        return ' '.join(result_sentences)
    
    def _create_abstract(
        self, 
        content: str, 
        max_tokens: int
    ) -> str:
        """Create high-level abstract of content."""
        sentences = self._split_sentences(content)
        abstract_sentences = []
        current_tokens = 0
        
        # Select sentences at strategic positions
        total_sentences = len(sentences)
        for idx, sentence in enumerate(sentences):
            # First sentence, middle, and last sentence
            if idx == 0 or idx == total_sentences // 2 or idx == total_sentences - 1:
                tokens = self._estimate_tokens(sentence)
                if current_tokens + tokens <= max_tokens:
                    abstract_sentences.append(sentence)
                    current_tokens += tokens
        
        return ' '.join(abstract_sentences)
    
    async def decompress_context(
        self,
        context_id: str,
        detail_level: Optional[str] = None,
        query: Optional[str] = None
    ) -> DecompressionResult:
        """
        Decompress context at specified detail level.
        
        Args:
            context_id: ID of compressed context
            detail_level: Level of detail (critical, important, abstract, full)
            query: Optional query for relevance scoring
            
        Returns:
            DecompressionResult object
        """
        start_time = time.time()
        
        if context_id not in self.compressed_storage:
            raise ValueError(f"Context ID {context_id} not found")
        
        compressed_ctx = self.compressed_storage[context_id]
        
        # Determine detail level
        if detail_level is None:
            detail_level = self._determine_detail_level(query)
        
        # Get content based on detail level
        content = ""
        source_level = None
        
        if detail_level == "critical":
            content = compressed_ctx.critical_content
            source_level = CompressionLevel.CRITICAL
        elif detail_level == "important":
            content = compressed_ctx.important_summary
            source_level = CompressionLevel.IMPORTANT
        elif detail_level == "abstract":
            content = compressed_ctx.abstract_summary
            source_level = CompressionLevel.ABSTRACT
        else:
            # Full decompression
            content = lz4.frame.decompress(compressed_ctx.full_compressed).decode('utf-8')
            source_level = None
        
        # Calculate relevance score if query provided
        relevance_score = 0.0
        if query and self.embedder:
            relevance_score = await self._calculate_relevance(query, content)
        
        decompression_time = time.time() - start_time
        self.compression_stats["decompression_time"] += decompression_time
        
        return DecompressionResult(
            content=content,
            detail_level=detail_level,
            relevance_score=relevance_score,
            decompression_time=decompression_time,
            source_level=source_level
        )
    
    def _determine_detail_level(self, query: Optional[str]) -> str:
        """Determine appropriate detail level based on query."""
        if query is None:
            return "important"
        
        # Check if query needs specific details
        query_lower = query.lower()
        
        detail_indicators = [
            'detailed', 'specific', 'exact', 'verbatim', 
            'quote', 'precise', 'comprehensive'
        ]
        
        if any(indicator in query_lower for indicator in detail_indicators):
            return "important"
        else:
            return "abstract"
    
    async def _calculate_relevance(self, query: str, content: str) -> float:
        """Calculate relevance score between query and content."""
        if not self.embedder:
            return 0.5
        
        try:
            # Generate embeddings
            query_embeddings = self._get_embeddings([query])
            content_embeddings = self._get_embeddings([content])
            
            if not query_embeddings or not content_embeddings:
                return 0.5
            
            query_embedding = np.array(query_embeddings[0])
            content_embedding = np.array(content_embeddings[0])
            
            # Calculate cosine similarity
            similarity = float(np.dot(query_embedding, content_embedding) / (
                np.linalg.norm(query_embedding) * np.linalg.norm(content_embedding)
            ))
            
            return max(0.0, min(1.0, similarity))
        except Exception as e:
            logger.warning(f"Relevance calculation failed: {e}")
            return 0.5
    
    async def get_compression_stats(self) -> Dict[str, Any]:
        """Get compression performance statistics."""
        if self.compression_stats["total_compressed"] > 0:
            avg_compression = (
                self.compression_stats["total_compressed_tokens"] /
                self.compression_stats["total_original_tokens"]
            )
            self.compression_stats["average_compression_ratio"] = avg_compression
        
        return self.compression_stats.copy()
    
    async def batch_compress(
        self,
        contexts: List[str],
        metadata_list: Optional[List[Dict[str, Any]]] = None
    ) -> List[CompressedContext]:
        """Batch compress multiple contexts."""
        if metadata_list is None:
            metadata_list = [{} for _ in range(len(contexts))]
        
        tasks = []
        for context, metadata in zip(contexts, metadata_list):
            task = self.compress_context(context, metadata=metadata)
            tasks.append(task)
        
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    async def batch_decompress(
        self,
        context_ids: List[str],
        detail_level: Optional[str] = None,
        query: Optional[str] = None
    ) -> List[DecompressionResult]:
        """Batch decompress multiple contexts."""
        tasks = []
        for context_id in context_ids:
            task = self.decompress_context(context_id, detail_level, query)
            tasks.append(task)
        
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    def list_compressed_contexts(self) -> List[Dict[str, Any]]:
        """List all compressed contexts with metadata."""
        contexts = []
        for ctx in self.compressed_storage.values():
            contexts.append({
                "context_id": ctx.context_id,
                "original_size": ctx.original_size,
                "compressed_size": ctx.compressed_size,
                "compression_ratio": ctx.compression_ratio,
                "timestamp": ctx.timestamp,
                "metadata": ctx.metadata
            })
        return contexts
    
    def delete_compressed_context(self, context_id: str):
        """Delete a compressed context."""
        if context_id in self.compressed_storage:
            del self.compressed_storage[context_id]
            self._save_compressed_storage()
    
    def clear_all(self):
        """Clear all compressed contexts."""
        self.compressed_storage.clear()
        self._save_compressed_storage()
    
    @property
    def total_compressed(self) -> int:
        """Number of compressed contexts."""
        return len(self.compressed_storage)
    
    def __repr__(self) -> str:
        """String representation."""
        return (f"ContextCompressor(compressed={self.total_compressed}, "
                f"avg_ratio={self.compression_stats.get('average_compression_ratio', 0):.2f})")
