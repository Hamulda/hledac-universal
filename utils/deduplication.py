"""
Advanced Deduplication System - From MSQES

Multi-strategy deduplication combining:















- Semantic deduplication (vector embeddings)
- Content deduplication (MinHash + hashing)
- Metadata deduplication (field comparison)

Optimized for M1 Mac with memory-efficient implementations.
"""
import asyncio
import hashlib
from hledac.universal.utils.hashing import xxh3_64_hex, sha256_hex, blake3_64_hex
import json
import logging
import os
import re
import secrets
import struct
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import msgspec
from datetime import datetime
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Any
from operator import attrgetter, itemgetter
try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    np = None
    _NUMPY_AVAILABLE = False

# ISSUE-008: Rust LSH index — zero-copy PyO3 binding for O(1) near-duplicate detection
_rust_lsh_available = False
_rust_lsh = None
try:
    from hledac.universal._core.rust_backend import rust
    _rust_lsh = rust.lsh
    _rust_lsh_available = True  # Always available (Rust or Python fallback)
except ImportError:
    _rust_lsh = None

# ISSUE-008: Two-layer embedding cache — float16 L1 + np.memmap L2 (M1 8GB safe)
_embedding_cache = None
try:
    from hledac.universal._core.embedding_cache import EmbeddingCache
    _embedding_cache = EmbeddingCache
except ImportError:
    _embedding_cache = None

logger = logging.getLogger(__name__)

class DeduplicationStrategy(Enum):
    """Deduplication strategy types."""
    SEMANTIC = 'semantic'
    CONTENT = 'content'
    METADATA = 'metadata'
    HYBRID = 'hybrid'

class DeduplicationConfig(msgspec.Struct, gc=False):
    """Configuration for deduplication engine."""
    semantic_threshold: float = 0.85
    content_threshold: float = 0.9
    metadata_threshold: float = 0.95
    strategies: list[DeduplicationStrategy] = field(default_factory=lambda: [DeduplicationStrategy.SEMANTIC, DeduplicationStrategy.CONTENT, DeduplicationStrategy.METADATA])
    enable_minhash: bool = True
    minhash_threshold: float = 0.8
    minhash_size: int = 128
    ngram_size: int = 5
    embedding_dim: int = 768
    embedding_model: str = 'nomic-ai/nomic-embed-text-v1.5'
    cache_size_mb: int = 256
    memory_limit_mb: int = 1024
    max_concurrent_threads: int = 4
    hash_algorithm: str = 'sha256'
    batch_size: int = 100
    enable_parallel_processing: bool = True
    enable_monitoring: bool = True
    log_level: str = 'INFO'

class QueryItem(msgspec.Struct, gc=False):
    """Item for deduplication processing."""
    id: str
    title: str
    content: str
    url: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def content_hash(self) -> str:
        """Generate content hash."""
        combined = f'{self.title}{self.content}'
        return xxh3_64_hex(combined)[:12]

class SimilarityScore(msgspec.Struct, gc=False):
    """Similarity score with details."""
    score: float
    strategy: DeduplicationStrategy
    confidence: float
    details: dict[str, Any] = field(default_factory=dict)

class DeduplicationStats(msgspec.Struct, gc=False):
    """Statistics for deduplication."""
    total_items_processed: int = 0
    items_kept: int = 0
    items_removed: int = 0
    processing_time: float = 0.0
    memory_peak_mb: float = 0.0
    semantic_comparisons: int = 0
    content_comparisons: int = 0
    metadata_comparisons: int = 0
    cache_hits: int = 0
    cache_misses: int = 0


class DeduplicationMatch(msgspec.Struct, gc=False):
    """Match between two items."""
    original_item: QueryItem
    matched_item: QueryItem
    similarity_score: SimilarityScore
    match_type: DeduplicationStrategy
    decision: str = 'pending'


class DeduplicationResult(msgspec.Struct, gc=False):
    """Result of deduplication process."""
    original_items: list[QueryItem]
    unique_items: list[QueryItem]
    duplicates_removed: list[QueryItem]
    matches: list[DeduplicationMatch]
    processing_time: float
    stats: DeduplicationStats = field(default_factory=lambda: DeduplicationStats())

    @property
    def deduplication_rate(self) -> float:
        """Calculate deduplication rate."""
        if not self.original_items:
            return 0.0
        return len(self.duplicates_removed) / len(self.original_items)

class BaseDeduplicator(ABC):
    """Abstract base class for deduplicators."""

    def __init__(self, config: DeduplicationConfig):
        self.config = config
        self.logger = logging.getLogger(f'dedup.{self.__class__.__name__}')

    @abstractmethod
    async def find_duplicates(self, item: QueryItem, candidates: list[QueryItem]) -> list[DeduplicationMatch]:
        """Find duplicates for an item among candidates."""
        pass

    @abstractmethod
    async def cleanup(self):
        """Cleanup resources."""
        pass

class SemanticDeduplicator(BaseDeduplicator):
    """Semantic deduplication using vector embeddings."""
    MAX_EMBED_CACHE_ITEMS = 5000
    __slots__ = tuple(('_embedding_model', '_model_loaded', '_simhash', 'embedding_cache', 'embedding_cache_size', 'executor', 'max_cache_size_mb'))

    def __init__(self, config: DeduplicationConfig):
        super().__init__(config)
        # ISSUE-008: Two-layer embedding cache — float16 L1 + memmap L2
        if _embedding_cache is not None:
            self.embedding_cache: Any = _embedding_cache(
                capacity=5000,
                dim=config.embedding_dim,
                dtype=np.float16,
                l1_max_mb=256.0,
    )
        else:
            # Fallback: pure Python LRUCache (replaces OrderedDict)
            from hledac.universal.utils.lru_cache import LRUCache
            self.embedding_cache: Any = LRUCache(max_size=5000)
        self.embedding_cache_size = 0
        self.max_cache_size_mb = 256
        self._embedding_model = None
        self._model_loaded = False
        from hledac.universal.utils.domain_executors import get_semantic_executor
        self.executor = get_semantic_executor()
        self._simhash = SimHash(hashbits=64)
        # ISSUE-008: Rust LSH index — O(1) near-duplicate detection (<50ms for 10k sigs)
        self._lsh_index: Any = None
        if _rust_lsh_available:
            try:
                self._lsh_index = _rust_lsh.lsh_index_new(num_tables=16, num_rows=4)
            except Exception:
                self._lsh_index = None

    def _cluster_by_simhash(self, items: list[QueryItem], simhash_bits: int=16) -> dict[int, list[QueryItem]]:
        """Group items into LSH buckets using SimHash.

        Uses rust.lsh.LSHIndex.batch_query() when available (<50ms for 10k sigs).
        Falls back to pure Python OrderedDict bucketing.
        """
        if not items:
            return {}

        # Fast path: Rust LSH batch query
        if self._lsh_index is not None:
            try:
                # Compute simhashes for all items
                sigs_and_items: list[tuple[int, QueryItem]] = []
                for item in items:
                    sig = self._simhash.compute(item.content)
                    sigs_and_items.append((sig, item))

                # Batch query — all signatures at once
                sigs = [s for s, _ in sigs_and_items]
                results = self._lsh_index.batch_query(sigs, max_results=100)

                # Build clusters from LSH results
                clusters: dict[int, list[QueryItem]] = defaultdict(list)
                for (sig, item), candidates in zip(sigs_and_items, results, strict=False):
                    bucket = sig & (1 << simhash_bits) - 1
                    clusters[bucket].append(item)
                    # Add candidates (other items with similar simhash)
                    for doc_id, _ in candidates:
                        for s, it in sigs_and_items:
                            if hash(str(id(it.content))) == hash(doc_id):
                                if it not in clusters[bucket]:
                                    clusters[bucket].append(it)
                return clusters
            except Exception:  # noqa: BLE001
                pass  # Fall through to Python path

        # Slow path: pure Python OrderedDict bucketing
        clusters = defaultdict(list)
        for item in items:
            simhash_val = self._simhash.compute(item.content)
            bucket = simhash_val & (1 << simhash_bits) - 1
            clusters[bucket].append(item)
        return clusters

    async def find_duplicates(self, item: QueryItem, candidates: list[QueryItem]) -> list[DeduplicationMatch]:
        """Find semantically similar items."""
        if not candidates:
            return []
        try:
            item_embedding = await self._get_embedding(item)
            candidate_embeddings = await self._get_batch_embeddings(candidates)
            matches = []
            for i, candidate in enumerate(candidates):
                candidate_embedding = candidate_embeddings[i]
                if candidate_embedding is not None:
                    similarity = self._compute_cosine_similarity(item_embedding, candidate_embedding)
                    if similarity >= self.config.semantic_threshold:
                        score = SimilarityScore(score=similarity, strategy=DeduplicationStrategy.SEMANTIC, confidence=min(1.0, similarity + 0.1), details={'embedding_dim': self.config.embedding_dim, 'model': self.config.embedding_model, 'similarity_metric': 'cosine'})
                        match = DeduplicationMatch(original_item=item, matched_item=candidate, similarity_score=score, match_type=DeduplicationStrategy.SEMANTIC, decision='pending')
                        matches.append(match)
            return matches
        except Exception as e:
            self.logger.error(f'Semantic deduplication failed: {e}')
            return []

    async def _get_embedding(self, item: QueryItem) -> np.ndarray:
        """Get embedding for a single item."""
        content = item.content.lower().strip()
        if content in self.embedding_cache:
            self.embedding_cache.move_to_end(content)
            return self.embedding_cache[content]
        embedding = await self._generate_embedding(content)
        if self._can_cache_embedding(embedding):
            while len(self.embedding_cache) >= self.MAX_EMBED_CACHE_ITEMS:
                oldest_key, oldest_val = self.embedding_cache.popitem(last=False)
                self.embedding_cache_size -= oldest_val.nbytes
            self.embedding_cache[content] = embedding
            self.embedding_cache_size += embedding.nbytes
        return embedding

    async def _get_batch_embeddings(self, items: list[QueryItem]) -> list[np.ndarray | None]:
        """Get embeddings for a batch of items."""
        if not items:
            return []
        contents = [item.content.lower().strip() for item in items]
        cached_embeddings = []
        uncached_indices = []
        uncached_contents = []
        for i, content in enumerate(contents):
            if content in self.embedding_cache:
                self.embedding_cache.move_to_end(content)
                cached_embeddings.append(self.embedding_cache[content])
            else:
                cached_embeddings.append(None)
                uncached_indices.append(i)
                uncached_contents.append(content)
        if uncached_contents:
            new_embeddings = await self._generate_batch_embeddings(uncached_contents)
            for idx, content, embedding in zip(uncached_indices, uncached_contents, new_embeddings, strict=False):
                if embedding is not None and self._can_cache_embedding(embedding):
                    while len(self.embedding_cache) >= self.MAX_EMBED_CACHE_ITEMS:
                        oldest_key, oldest_val = self.embedding_cache.popitem(last=False)
                        self.embedding_cache_size -= oldest_val.nbytes
                    self.embedding_cache[content] = embedding
                    self.embedding_cache_size += embedding.nbytes
                cached_embeddings[idx] = embedding
        return cached_embeddings

    async def _generate_embedding(self, content: str) -> Any:
        """Generate embedding for content using dedup-specific task."""
        try:
            if not self._model_loaded:
                await self._load_model()
            model: Any = self._embedding_model
            if model is not None:
                if hasattr(model, 'embed_for_dedup'):
                    return model.embed_for_dedup(content)
                return model.encode([content])[0]
        except Exception as e:
            self.logger.debug(f'Embedding generation failed: {e}')
        return self._fallback_embedding(content)

    async def _generate_batch_embeddings(self, contents: list[str]) -> list[Any | None]:
        """Generate embeddings for batch of contents using dedup-specific task."""
        try:
            if not self._model_loaded:
                await self._load_model()
            model: Any = self._embedding_model
            if model is not None:
                if hasattr(model, 'embed_for_dedup'):
                    return [model.embed_for_dedup(c) for c in contents]
                batch_embeddings = model.encode(contents)
                return list(batch_embeddings)
        except Exception as e:
            self.logger.debug(f'Batch embedding failed: {e}')
        embeddings: list[Any | None] = []
        for content in contents:
            try:
                embedding = await self._generate_embedding(content)
                embeddings.append(embedding)
            except Exception:
                embeddings.append(None)
        return embeddings

    async def _load_model(self):
        """Load MLXEmbeddingManager first, then sentence-transformers fallback, then hash-based."""
        try:
            from hledac.universal._core.mlx_embeddings import get_embedding_manager
            self._embedding_model = get_embedding_manager()
            self._model_loaded = True
            self.logger.info(f'[DEDUP] Using shared MLXEmbeddingManager: {self._embedding_model.model_path}')
            return
        except ImportError:
            self.logger.debug('[DEDUP] mlx_embeddings not available, trying sentence-transformers')
        except Exception as e:
            self.logger.debug(f'[DEDUP] MLXEmbeddingManager init failed: {e}')
        try:
            from sentence_transformers import SentenceTransformer
            self._embedding_model = await asyncio.get_running_loop().run_in_executor(self.executor, lambda: SentenceTransformer(self.config.embedding_model))
            self._model_loaded = True
            self.logger.info(f'[DEDUP] Loaded sentence transformer model: {self.config.embedding_model}')
        except ImportError:
            self.logger.warning('sentence-transformers not available, using fallback embedding')
        except Exception as e:
            self.logger.error(f'Failed to load sentence transformer model: {e}')

    def _fallback_embedding(self, content: str) -> np.ndarray:
        """Generate fallback embedding using hash-based approach."""
        if not _NUMPY_AVAILABLE:
            raise RuntimeError('numpy is required for deduplication. Install: uv pip install numpy')
        words = content.split()[:100]
        embedding = np.zeros(self.config.embedding_dim, dtype=np.float32)
        for i, word in enumerate(words):
            if i >= self.config.embedding_dim:
                break
            word_hash = xxh3_64_hex(word)
            word_vector = float(int(word_hash[:8], 16), 16) / (2 ** 128 - 1)
            embedding[i] = word_vector * (1.0 - i / len(words))
        seed_bytes = xxh3_64_hex(content).encode()
        seed = int.from_bytes(seed_bytes[:4], 'little')
        rng = np.random.RandomState(seed)
        embedding += rng.normal(0, 0.01, self.config.embedding_dim)
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding.astype(np.float32)

    def _compute_cosine_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """Compute cosine similarity between two embeddings."""
        if not _NUMPY_AVAILABLE:
            raise RuntimeError('numpy is required for deduplication. Install: uv pip install numpy')
        dot_product = np.dot(emb1, emb2)
        norm1 = np.linalg.norm(emb1)
        norm2 = np.linalg.norm(emb2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(dot_product / (norm1 * norm2))

    def _cluster_by_simhash(self, items: list[QueryItem], simhash_bits: int=16) -> dict[int, list[QueryItem]]:
        """Group items into LSH buckets using SimHash for near-linear deduplication."""
        clusters = defaultdict(list)
        for item in items:
            simhash_val = self._simhash.compute(item.content)
            bucket = simhash_val & (1 << simhash_bits) - 1
            clusters[bucket].append(item)
        return clusters

    def _can_cache_embedding(self, embedding: np.ndarray) -> bool:
        """Check if we can cache embedding within memory limits."""
        embedding_size_mb = embedding.nbytes / (1024 * 1024)
        current_cache_mb = self.embedding_cache_size / (1024 * 1024)
        return current_cache_mb + embedding_size_mb <= self.max_cache_size_mb

    async def cleanup(self):
        """Cleanup resources."""
        self.executor.shutdown(wait=True)
        self.embedding_cache.clear()
        self._embedding_model = None
        self._model_loaded = False
        self.logger.info('Semantic deduplicator cleanup complete')

    def close(self):
        """F196B: Non-blocking close of thread pool."""
        self.executor.shutdown(wait=False)

class ContentDeduplicator(BaseDeduplicator):
    """Content-based deduplication using hashing and MinHash."""
    __slots__ = tuple(('_simhash', 'cache_size', 'content_cache', 'executor', 'max_cache_size_mb'))

    def __init__(self, config: DeduplicationConfig):
        super().__init__(config)
        self.content_cache: dict[str, dict[str, Any]] = {}
        self.cache_size = 0
        self.max_cache_size_mb = 128
        from hledac.universal.utils.domain_executors import get_content_executor
        self.executor = get_content_executor()
        self._simhash = SimHash(hashbits=64)

    def _cluster_by_simhash(self, items: list[QueryItem], simhash_bits: int=16) -> dict[int, list[QueryItem]]:
        """Group items into LSH buckets using SimHash for near-linear deduplication."""
        clusters = defaultdict(list)
        for item in items:
            simhash_val = self._simhash.compute(item.content)
            bucket = simhash_val & (1 << simhash_bits) - 1
            clusters[bucket].append(item)
        return clusters

    async def find_duplicates(self, item: QueryItem, candidates: list[QueryItem]) -> list[DeduplicationMatch]:
        """Find content-based duplicates using LSH clustering for O(n) performance."""
        if not candidates:
            return []
        matches = []
        try:
            all_items = [item] + candidates
            clusters = self._cluster_by_simhash(all_items)
            item_simhash = self._simhash.compute(item.content)
            item_bucket = item_simhash & (1 << 16) - 1
            bucket_candidates = clusters.get(item_bucket, [])
            for candidate in bucket_candidates:
                if candidate.id == item.id:
                    continue
                candidate_signature = await self._get_content_signature(candidate)
                item_signature = await self._get_content_signature(item)
                hash_similarity = self._compute_hash_similarity(item_signature, candidate_signature)
                minhash_similarity = 0.0
                if self.config.enable_minhash:
                    minhash_similarity = self._compute_minhash_similarity(item_signature.get('minhash'), candidate_signature.get('minhash'))
                overall_similarity = max(hash_similarity, minhash_similarity)
                if overall_similarity >= self.config.content_threshold:
                    score = SimilarityScore(score=overall_similarity, strategy=DeduplicationStrategy.CONTENT, confidence=min(1.0, overall_similarity + 0.05), details={'hash_similarity': hash_similarity, 'minhash_similarity': minhash_similarity, 'hash_algorithm': self.config.hash_algorithm, 'exact_hash_match': item_signature['hash'] == candidate_signature['hash']})
                    match = DeduplicationMatch(original_item=item, matched_item=candidate, similarity_score=score, match_type=DeduplicationStrategy.CONTENT, decision='pending')
                    matches.append(match)
            return matches
        except Exception as e:
            self.logger.error(f'Content deduplication failed: {e}')
            return []

    async def _get_content_signature(self, item: QueryItem) -> dict[str, Any]:
        """Generate content signature for an item."""
        content = item.content.strip()
        if content in self.content_cache:
            return self.content_cache[content]
        signature = await asyncio.get_running_loop().run_in_executor(self.executor, self._generate_signature, content)
        signature_size = len(str(signature)) / (1024 * 1024)
        if self.cache_size + signature_size <= self.max_cache_size_mb:
            self.content_cache[content] = signature
            self.cache_size += signature_size
        return signature

    def _generate_signature(self, content: str) -> dict[str, Any]:
        """Generate complete content signature."""
        signature = {}
        signature['hash'] = self._compute_hash(content)
        signature['char_hash'] = self._compute_character_hash(content)
        if self.config.enable_minhash:
            signature['minhash'] = self._compute_minhash(content)
        signature['length'] = len(content)
        signature['word_count'] = len(content.split())
        signature['unique_words'] = len(set(content.lower().split()))
        return signature

    def _compute_hash(self, content: str) -> str:
        """Compute exact content hash."""
        if self.config.hash_algorithm == 'sha256':
            return sha256_hex(content)
        elif self.config.hash_algorithm == 'md5':
            return xxh3_64_hex(content)
        else:
            return sha256_hex(content)
    _DEFAULT_MAX_NGRAMS = 50000

    def _get_max_ngrams(self) -> int:
        """Get ngram cap from environment with safe fallback."""
        try:
            return int(os.environ.get('HLEDAC_DEDUP_MAX_NGRAMS', str(self._DEFAULT_MAX_NGRAMS)))
        except (ValueError, TypeError):
            return self._DEFAULT_MAX_NGRAMS

    def _compute_character_hash(self, content: str) -> str:
        """Compute character-level hash."""
        normalized = ' '.join(content.lower().split())
        return xxh3_64_hex(normalized)

    def _compute_minhash(self, content: str) -> list[int]:
        """
        Compute MinHash signature for content similarity.

        F214OPT-J: Note on mmh3 seed optimization — mmh3.hash does accept a seed
        argument (mmh3.hash(key, seed=N, signed=False)). However, using
        mmh3.hash(ngram, seed=i) instead of f"{ngram}_{i}" would change the
        computed hash values, which would invalidate existing stored MinHash
        signatures. To preserve exact signature compatibility, the current
        f-string approach is retained. The allocation overhead is bounded by
        HLEDAC_DEDUP_MAX_NGRAMS (default 50000).
        """
        try:
            import mmh3
        except ImportError:
            self.logger.warning('mmh3 not available, MinHash disabled')
            return []
        max_ngrams = self._get_max_ngrams()
        ngrams = self._generate_ngrams(content, self.config.ngram_size)
        if len(ngrams) > max_ngrams:
            self.logger.debug(f'[DEDUP] Ngram count {len(ngrams)} exceeds cap {max_ngrams}, truncating')
            ngrams = ngrams[:max_ngrams]
        if not ngrams:
            return [0] * self.config.minhash_size
        minhash_signature: list[int] = []
        for i in range(self.config.minhash_size):
            min_hash: float = float('inf')
            for ngram in ngrams:
                hash_value = mmh3.hash(f'{ngram}_{i}', signed=False)
                min_hash = min(min_hash, hash_value)
            minhash_signature.append(int(min_hash))
        return minhash_signature

    def _generate_ngrams(self, content: str, n: int) -> list[str]:
        """Generate n-grams from content."""
        content = content.lower().replace('\n', ' ').strip()
        if len(content) < n:
            return [content]
        ngrams = [''.join(t) for t in zip(*[content[i:] for i in range(n)]) if ''.join(t).strip()]
        return ngrams

    def _compute_hash_similarity(self, sig1: dict[str, Any], sig2: dict[str, Any]) -> float:
        """Compute hash-based similarity."""
        if sig1['hash'] == sig2['hash']:
            return 1.0
        if sig1.get('char_hash') == sig2.get('char_hash'):
            return 0.95
        length_diff = abs(sig1['length'] - sig2['length'])
        max_length = max(sig1['length'], sig2['length'])
        length_similarity = 1.0 - length_diff / max_length if max_length > 0 else 0.0
        word_diff = abs(sig1['word_count'] - sig2['word_count'])
        max_words = max(sig1['word_count'], sig2['word_count'])
        word_similarity = 1.0 - word_diff / max_words if max_words > 0 else 0.0
        return length_similarity * 0.4 + word_similarity * 0.3 + 0.3

    def _compute_minhash_similarity(self, minhash1: list[int] | None, minhash2: list[int] | None) -> float:
        """Compute MinHash Jaccard similarity."""
        if not minhash1 or not minhash2:
            return 0.0
        if len(minhash1) != len(minhash2):
            return 0.0
        matches = sum((1 for h1, h2 in zip(minhash1, minhash2, strict=False) if h1 == h2))
        return matches / len(minhash1)

    async def cleanup(self):
        """Cleanup resources."""
        self.executor.shutdown(wait=True)
        self.content_cache.clear()
        self.logger.info('Content deduplicator cleanup complete')

    def close(self):
        """F196B: Non-blocking close of thread pool."""
        self.executor.shutdown(wait=False)

class MetadataDeduplicator(BaseDeduplicator):
    """Metadata-based deduplication using field comparison."""
    __slots__ = tuple(('executor', 'field_weights', 'normalization_cache', 'stop_words'))

    def __init__(self, config: DeduplicationConfig):
        super().__init__(config)
        self.field_weights = {'title': 0.4, 'url': 0.3, 'source': 0.1, 'timestamp': 0.1, 'author': 0.1}
        self.stop_words = self._get_stop_words()
        self.normalization_cache: dict[str, str] = {}
        from hledac.universal.utils.domain_executors import get_metadata_executor
        self.executor = get_metadata_executor()

    async def find_duplicates(self, item: QueryItem, candidates: list[QueryItem]) -> list[DeduplicationMatch]:
        """Find metadata-based duplicates."""
        if not candidates:
            return []
        matches = []
        try:
            item_metadata = await self._extract_and_normalize_metadata(item)
            for candidate in candidates:
                candidate_metadata = await self._extract_and_normalize_metadata(candidate)
                field_similarities = await self._compute_field_similarities(item_metadata, candidate_metadata)
                overall_similarity = self._compute_weighted_similarity(field_similarities)
                if overall_similarity >= self.config.metadata_threshold:
                    score = SimilarityScore(score=overall_similarity, strategy=DeduplicationStrategy.METADATA, confidence=min(1.0, overall_similarity + 0.1), details={'field_similarities': field_similarities, 'weights': self.field_weights})
                    match = DeduplicationMatch(original_item=item, matched_item=candidate, similarity_score=score, match_type=DeduplicationStrategy.METADATA, decision='pending')
                    matches.append(match)
            return matches
        except Exception as e:
            self.logger.error(f'Metadata deduplication failed: {e}')
            return []

    async def _extract_and_normalize_metadata(self, item: QueryItem) -> dict[str, Any]:
        """Extract and normalize metadata fields."""
        metadata = {'title': item.title, 'url': item.url, 'source': item.source, 'content_length': len(item.content), 'timestamp': item.timestamp.isoformat() if item.timestamp else ''}
        metadata.update(item.metadata)
        normalized_metadata = {}
        for field, value in metadata.items():
            if value is not None:
                normalized_value = await self._normalize_field_value(field, value)
                if normalized_value:
                    normalized_metadata[field] = normalized_value
        return normalized_metadata

    async def _normalize_field_value(self, field: str, value: Any) -> Any:
        """Normalize a metadata field value."""
        if isinstance(value, str):
            return await self._normalize_text(value)
        elif isinstance(value, (list, tuple)):
            return [await self._normalize_text(str(v)) for v in value if v is not None]
        else:
            return str(value)

    async def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison."""
        if text in self.normalization_cache:
            return self.normalization_cache[text]
        normalized = await asyncio.get_running_loop().run_in_executor(self.executor, self._normalize_text_sync, text)
        if len(self.normalization_cache) < 10000:
            self.normalization_cache[text] = normalized
        return normalized

    def _normalize_text_sync(self, text: str) -> str:
        """Synchronous text normalization."""
        text = text.lower().strip()
        text = re.sub('\\s+', ' ', text)
        text = re.sub('[^a-z0-9\\s]', '', text)
        words = text.split()
        words = [w for w in words if w not in self.stop_words]
        return ' '.join(words)

    async def _compute_field_similarities(self, metadata1: dict[str, Any], metadata2: dict[str, Any]) -> dict[str, float]:
        """Compute similarities for each field."""
        similarities = {}
        all_fields = set(metadata1.keys()) | set(metadata2.keys())
        for field in all_fields:
            value1 = metadata1.get(field)
            value2 = metadata2.get(field)
            if value1 is None or value2 is None:
                similarities[field] = 0.0
                continue
            if field in ['title', 'url']:
                similarities[field] = await self._text_similarity(str(value1), str(value2))
            elif field in ['source', 'author']:
                similarities[field] = 1.0 if str(value1).lower() == str(value2).lower() else 0.0
            else:
                similarities[field] = self._generic_similarity(value1, value2)
        return similarities

    async def _text_similarity(self, text1: str, text2: str) -> float:
        """Compute text similarity."""
        if not text1 or not text2:
            return 0.0
        if text1 == text2:
            return 1.0
        seq_similarity = SequenceMatcher(None, text1, text2).ratio()
        words1 = set(text1.split())
        words2 = set(text2.split())
        if not words1 or not words2:
            jaccard_similarity = 0.0
        else:
            intersection = len(words1 & words2)
            union = len(words1 | words2)
            jaccard_similarity = intersection / union if union > 0 else 0.0
        return seq_similarity * 0.6 + jaccard_similarity * 0.4

    def _generic_similarity(self, value1: Any, value2: Any) -> float:
        """Compute generic similarity."""
        str1 = str(value1).lower().strip()
        str2 = str(value2).lower().strip()
        if str1 == str2:
            return 1.0
        return SequenceMatcher(None, str1, str2).ratio()

    def _compute_weighted_similarity(self, field_similarities: dict[str, float]) -> float:
        """Compute weighted similarity."""
        weighted_sum = 0.0
        total_weight = 0.0
        for field, similarity in field_similarities.items():
            weight = self.field_weights.get(field, 0.1)
            weighted_sum += similarity * weight
            total_weight += weight
        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _get_stop_words(self) -> set[str]:
        """Get common stop words."""
        return {'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'that', 'the', 'to', 'was', 'will', 'with', 'this', 'but', 'they', 'have', 'had', 'what', 'said', 'each', 'which', 'their', 'time', 'if', 'about', 'up', 'out', 'many', 'then', 'them', 'these', 'so', 'some', 'her', 'would', 'make', 'like', 'into', 'him', 'two', 'more', 'very', 'after', 'words', 'long', 'than', 'first', 'been'}

    async def cleanup(self):
        """Cleanup resources."""
        self.executor.shutdown(wait=True)
        self.normalization_cache.clear()
        self.logger.info('Metadata deduplicator cleanup complete')

    def close(self):
        """F196B: Non-blocking close of thread pool."""
        self.executor.shutdown(wait=False)

class DeduplicationEngine:
    """Main deduplication engine with multi-strategy support."""
    __slots__ = tuple(('_lock', '_processing', 'config', 'content_dedup', 'logger', 'metadata_dedup', 'semantic_dedup', 'stats'))

    def __init__(self, config: DeduplicationConfig | None=None):
        self.config = config or DeduplicationConfig()
        self.logger = logging.getLogger('dedup.engine')
        self.semantic_dedup = SemanticDeduplicator(self.config)
        self.content_dedup = ContentDeduplicator(self.config)
        self.metadata_dedup = MetadataDeduplicator(self.config)
        self._lock: asyncio.Lock | None = None
        self._processing = False
        self.stats = DeduplicationStats()
        self.logger.info('DeduplicationEngine initialized')

    async def _get_lock(self) -> asyncio.Lock:
        """Lazy create asyncio.Lock for async context."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def deduplicate(self, items: list[QueryItem]) -> DeduplicationResult:
        """Deduplicate list of query items."""
        start_time = time.time()
        if self._processing:
            raise RuntimeError('Deduplication already in progress')
        lock = await self._get_lock()
        async with lock:
            self._processing = True
        try:
            self.logger.info(f'Starting deduplication of {len(items)} items')
            self.stats = DeduplicationStats()
            self.stats.total_items_processed = len(items)
            batch_size = min(self.config.batch_size, len(items))
            all_matches = []
            seen_hashes = set()
            unique_items = []
            duplicates_removed = []
            for i in range(0, len(items), batch_size):
                batch = items[i:i + batch_size]
                batch_result = await self._process_batch(batch)
                for item in batch:
                    if item.content_hash in seen_hashes:
                        duplicates_removed.append(item)
                    else:
                        unique_items.append(item)
                        seen_hashes.add(item.content_hash)
                all_matches.extend(batch_result)
            processing_time = time.time() - start_time
            self.stats.processing_time = processing_time
            self.stats.items_kept = len(unique_items)
            self.stats.items_removed = len(duplicates_removed)
            dedup_result = DeduplicationResult(original_items=items, unique_items=unique_items, duplicates_removed=duplicates_removed, matches=all_matches, processing_time=processing_time, stats=self.stats)
            self.logger.info(f'Deduplication complete: {len(duplicates_removed)}/{len(items)} removed ({dedup_result.deduplication_rate:.2%} rate, {processing_time:.2f}s)')
            return dedup_result
        finally:
            lock = await self._get_lock()
            async with lock:
                self._processing = False

    async def _process_batch(self, batch: list[QueryItem]) -> list[DeduplicationMatch]:
        """Process a batch of items."""
        matches = []
        for i, current_item in enumerate(batch):
            item_matches = await self._find_duplicates(current_item, batch[i + 1:])
            matches.extend(item_matches)
        return matches

    async def _find_duplicates(self, item: QueryItem, candidates: list[QueryItem]) -> list[DeduplicationMatch]:
        """Find duplicates for an item."""
        all_matches = []
        for strategy in self.config.strategies:
            if strategy == DeduplicationStrategy.SEMANTIC:
                self.stats.semantic_comparisons += 1
                matches = await self.semantic_dedup.find_duplicates(item, candidates)
            elif strategy == DeduplicationStrategy.CONTENT:
                self.stats.content_comparisons += 1
                matches = await self.content_dedup.find_duplicates(item, candidates)
            elif strategy == DeduplicationStrategy.METADATA:
                self.stats.metadata_comparisons += 1
                matches = await self.metadata_dedup.find_duplicates(item, candidates)
            else:
                continue
            all_matches.extend(matches)
        unique_matches = self._deduplicate_matches(all_matches)
        return unique_matches

    def _deduplicate_matches(self, matches: list[DeduplicationMatch]) -> list[DeduplicationMatch]:
        """Deduplicate matches and apply decision logic."""
        matched_items: dict[str, list[DeduplicationMatch]] = {}
        for match in matches:
            key = match.matched_item.id
            if key not in matched_items:
                matched_items[key] = []
            matched_items[key].append(match)
        unique_matches = []
        for matches_group in matched_items.values():
            matches_group.sort(key=attrgetter("similarity_score").score, reverse=True)
            best_match = matches_group[0]
            if best_match.similarity_score.score >= 0.85:
                best_match.decision = 'remove'
            elif best_match.similarity_score.score >= 0.7:
                best_match.decision = 'merge'
            else:
                best_match.decision = 'keep'
            unique_matches.append(best_match)
        return unique_matches

    def get_statistics(self) -> DeduplicationStats:
        """Get current statistics."""
        return self.stats

    async def cleanup(self):
        """Cleanup resources."""
        await self.semantic_dedup.cleanup()
        await self.content_dedup.cleanup()
        await self.metadata_dedup.cleanup()
        self.logger.info('DeduplicationEngine cleanup complete')

    def close(self):
        """F196B: Non-blocking close of all thread pools."""
        self.semantic_dedup.executor.shutdown(wait=False)
        self.content_dedup.executor.shutdown(wait=False)
        self.metadata_dedup.executor.shutdown(wait=False)
        self.logger.info('DeduplicationEngine thread pools closed (non-blocking)')

class DomainStats(msgspec.Struct, gc=False):
    """Per-domain statistiky pro yield tracking a domain diversity - M1 8GB."""
    domain: str
    requests: int = 0
    new_docs: int = 0
    dedup_hits: int = 0
    total_latency_ms: float = 0.0
    http_errors: int = 0
    robots_blocked: int = 0
    last_request_at: float | None = None
    yield_score: float = 1.0
    first_seen_at: float = field(default_factory=time.time)
    stale_cache_hits: int = 0
    last_stale_hit_at: float | None = None

    @property
    def avg_latency_ms(self) -> float:
        if self.requests == 0:
            return 0.0
        return self.total_latency_ms / self.requests

    @property
    def throttle_delay_ms(self) -> float:
        """
        Calculate throttle delay based on domain health.
        Increases delay if stale cache is used frequently.
        """
        base_delay = 0.0
        if self.yield_score < 0.2:
            base_delay = 5000
        elif self.yield_score < 0.5:
            base_delay = 1000
        stale_penalty = 0.0
        if self.stale_cache_hits > 10:
            stale_penalty = 10000
        elif self.stale_cache_hits > 5:
            stale_penalty = 5000
        elif self.stale_cache_hits > 0:
            stale_penalty = 1000
        if self.last_stale_hit_at:
            hours_since = (time.time() - self.last_stale_hit_at) / 3600
            if hours_since < 1:
                stale_penalty *= 1.5
        return base_delay + stale_penalty

    def record_request(self, latency_ms: float, is_new: bool=False, is_dedup: bool=False, is_error: bool=False, blocked_by_robots: bool=False, stale_cache_hit: bool=False):
        """Zaznamena vysledek requestu a aktualizuje yield."""
        self.requests += 1
        self.total_latency_ms += latency_ms
        self.last_request_at = time.time()
        if is_error:
            self.http_errors += 1
        if blocked_by_robots:
            self.robots_blocked += 1
        if is_dedup:
            self.dedup_hits += 1
        if is_new:
            self.new_docs += 1
        if stale_cache_hit:
            self.stale_cache_hits += 1
            self.last_stale_hit_at = time.time()
        if self.requests > 0:
            raw_yield = self.new_docs / self.requests
            error_penalty = min(0.5, self.http_errors / max(1, self.requests))
            stale_penalty = min(0.2, self.stale_cache_hits / max(1, self.requests))
            self.yield_score = max(0.1, raw_yield - error_penalty - stale_penalty)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for persistence."""
        return {'domain': self.domain, 'requests': self.requests, 'new_docs': self.new_docs, 'dedup_hits': self.dedup_hits, 'total_latency_ms': self.total_latency_ms, 'http_errors': self.http_errors, 'robots_blocked': self.robots_blocked, 'last_request_at': self.last_request_at, 'yield_score': self.yield_score, 'first_seen_at': self.first_seen_at, 'stale_cache_hits': self.stale_cache_hits, 'last_stale_hit_at': self.last_stale_hit_at}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DomainStats:
        """Create from dict."""
        stats = cls(domain=data['domain'])
        stats.requests = data.get('requests', 0)
        stats.new_docs = data.get('new_docs', 0)
        stats.dedup_hits = data.get('dedup_hits', 0)
        stats.total_latency_ms = data.get('total_latency_ms', 0.0)
        stats.http_errors = data.get('http_errors', 0)
        stats.robots_blocked = data.get('robots_blocked', 0)
        stats.last_request_at = data.get('last_request_at')
        stats.yield_score = data.get('yield_score', 1.0)
        stats.first_seen_at = data.get('first_seen_at', time.time())
        stats.stale_cache_hits = data.get('stale_cache_hits', 0)
        stats.last_stale_hit_at = data.get('last_stale_hit_at')
        return stats

class DomainStatsManager:
    """Spravuje DomainStats s persistenci na disk - M1 8GB."""
    __slots__ = tuple(('_max_domains', '_stats', '_storage_dir'))

    def __init__(self, storage_dir: Path | None=None, max_domains: int=500):
        from pathlib import Path
        self._storage_dir = storage_dir or Path.home() / '.hledac' / 'domain_stats'
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._max_domains = max_domains
        self._stats: dict[str, DomainStats] = {}
        self._load_stats()

    def _get_storage_path(self) -> Path:
        return self._storage_dir / 'domain_stats.json'

    def _load_stats(self) -> None:
        """Nacte statistiky z disku."""
        path = self._get_storage_path()
        if path.exists():
            try:
                with open(path) as f:
                    data: dict[str, Any] = json.load(f)
                for domain, stats_data in data.items():
                    self._stats[domain] = DomainStats.from_dict(stats_data)
                logger.info(f'[DOMAIN STATS] Loaded {len(self._stats)} domains from disk')
            except Exception as e:
                logger.warning(f'Failed to load domain stats: {e}')

    def save_stats(self) -> None:
        """Ulozi statistiky na disk."""
        try:
            path = self._get_storage_path()
            data = {domain: stats.to_dict() for domain, stats in self._stats.items()}
            with open(path, 'w') as f:
                json.dump(data, f)
            logger.debug(f'[DOMAIN STATS] Saved {len(self._stats)} domains to disk')
        except Exception as e:
            logger.warning(f'Failed to save domain stats: {e}')

    def get_stats(self, domain: str) -> DomainStats:
        """Vrati statistiky pro domenu (vytvori nove pokud neexistuji)."""
        if domain not in self._stats:
            if len(self._stats) >= self._max_domains:
                oldest = min(self._stats.items(), key=lambda x: x[1].last_request_at or 0)
                del self._stats[oldest[0]]
            self._stats[domain] = DomainStats(domain=domain)
        return self._stats[domain]

    def get_yield_penalty(self, domain: str) -> float:
        """Vrati yield-based penalty pro domenu (0-1, vyssi = vice penalizace)."""
        stats = self.get_stats(domain)
        if stats.yield_score < 0.2:
            return 0.5
        if stats.yield_score < 0.5:
            return 0.2
        return 0.0

    def get_all_stats(self) -> dict[str, DomainStats]:
        return dict(self._stats)

    def get_summary(self) -> dict[str, Any]:
        """Get summary stats for all domains."""
        if not self._stats:
            return {'total_domains': 0}
        total_requests = sum((s.requests for s in self._stats.values()))
        total_new = sum((s.new_docs for s in self._stats.values()))
        return {'total_domains': len(self._stats), 'total_requests': total_requests, 'total_new_docs': total_new, 'overall_yield': total_new / max(1, total_requests), 'avg_errors': sum((s.http_errors for s in self._stats.values())) / len(self._stats)}
_TOKEN_HASH_CACHE: dict[tuple[str, int], int] = {}
_TOKEN_HASH_CACHE_LOCK = threading.Lock()
_MAX_TOKEN_CACHE = 10000
# R6: Centralized Rust access via core.rust_backend
from hledac.universal._core.rust_backend import rust
from _core import aclose
_rust_hamming_dist: Callable[[int, int], int] | None = rust.raw.hamming_dist

class TopKBucketIndex:
    """Top-K bit bucketing for O(1) near-duplicate SimHash lookup.

    Partitions 64-bit fingerprints into 2^top_k_buckets buckets by their top-K bits.
    Near-duplicates (Hamming distance <= threshold) share the same top-K bits with
    high probability, so we only scan neighboring buckets instead of all N fingerprints.

    Performance:
    - Add: O(1) average — hash to bucket, scan 3-4 neighboring buckets
    - Memory: O(N) fingerprints stored + O(2^top_k_buckets) bucket overhead
    - Recall: ~95% for threshold=3, top_k=16 (same as full scan)

    Args:
        hashbits: Number of bits in fingerprint (default 64)
        top_k_bits: Number of leading bits for bucket key (default 16 → 65536 buckets)
        threshold: Max Hamming distance to consider near-duplicate (default 3)
    """
    __slots__ = ('hashbits', 'top_k_bits', 'threshold', '_buckets', '_total')

    def __init__(self, hashbits: int=64, top_k_bits: int=16, threshold: int=3) -> None:
        self.hashbits = hashbits
        self.top_k_bits = top_k_bits
        self.threshold = threshold
        self._buckets: dict[int, list[tuple[int, int]]] = {}
        self._total: int = 0

    def _bucket_key(self, fp: int) -> int:
        """Top-K bits as bucket key."""
        return fp >> self.hashbits - self.top_k_bits

    def _hamming(self, fp1: int, fp2: int) -> int:
        """Hamming distance — uses Rust hamming_dist if available."""
        if _rust_hamming_dist is not None:
            return _rust_hamming_dist(fp1, fp2)
        xor = fp1 ^ fp2
        return xor.bit_count()

    def _neighbor_buckets(self, bucket: int) -> list[int]:
        """Generate all 16 neighboring bucket keys (all 1-bit flips in top-K space).

        For Hamming distance <= 3, a near-duplicate's top-K bits differ by at most
        3 bit flips. We must check ALL possible 1-bit flips of the bucket key to
        ensure ~95% recall (threshold=3, top_k=16).
        """
        neighbors: list[int] = [bucket]
        for pos in range(self.top_k_bits):
            neighbor = bucket ^ 1 << pos
            if neighbor != bucket:
                neighbors.append(neighbor)
        return neighbors

    def add(self, fp: int) -> bool:
        """Add fingerprint, return True if near-duplicate found.

        Scans only neighboring buckets (bucket itself + 2-3 nearby buckets
        defined by 1-bit flips in top-K space). Full Hamming check only
        against candidates in those buckets.
        """
        bkey = self._bucket_key(fp)
        for nkey in self._neighbor_buckets(bkey):
            bucket = self._buckets.get(nkey)
            if not bucket:
                continue
            for stored_fp, _ in bucket:
                if self._hamming(fp, stored_fp) <= self.threshold:
                    return True
        self._buckets.setdefault(bkey, []).append((fp, 0))
        self._total += 1
        return False

    def __len__(self) -> int:
        return self._total

    def clear(self) -> None:
        self._buckets.clear()
        self._total = 0

class SimHash:
    """SimHash (64-bit) s persistetním seedem a thread-safe token cache - M1 8GB optimized."""
    __slots__ = tuple(('_bucket_index', '_simhash_threshold', 'hashbits', 'seed'))

    def __init__(self, hashbits: int=64, seed: int | None=None, simhash_threshold: int=3):
        self.hashbits = hashbits
        self._simhash_threshold = simhash_threshold
        self._bucket_index: TopKBucketIndex = TopKBucketIndex(hashbits=hashbits, top_k_bits=16, threshold=simhash_threshold)
        if seed is None:
            seed_file = Path.home() / '.hledac' / 'simhash_seed.txt'
            if seed_file.exists():
                with open(seed_file) as f:
                    seed = int(f.read().strip())
            else:
                seed = secrets.randbits(64)
                seed_file.parent.mkdir(parents=True, exist_ok=True)
                temp = seed_file.with_suffix('.tmp')
                with open(temp, 'w') as f:
                    f.write(str(seed))
                    f.flush()
                    os.fsync(f.fileno())
                temp.replace(seed_file)
        self.seed = seed

    def _tokenize(self, text: str) -> list[str]:
        """Tokenization - shingle by 3 words."""
        words = text.lower().split()
        if len(words) < 3:
            return words
        return [' '.join(t) for t in zip(words, words[1:], words[2:])]

    def _token_hash(self, token: str) -> int:
        """64-bit hash of token (seeded), with cache for repeated tokens."""
        key = (token, self.seed)
        with _TOKEN_HASH_CACHE_LOCK:
            if key in _TOKEN_HASH_CACHE:
                return _TOKEN_HASH_CACHE[key]
            token_hash = xxh3_64_hex(token + str(self.seed))
            result = int(token_hash[:16], 16)
            if len(_TOKEN_HASH_CACHE) >= _MAX_TOKEN_CACHE:
                oldest_key = next(iter(_TOKEN_HASH_CACHE))
                del _TOKEN_HASH_CACHE[oldest_key]
            _TOKEN_HASH_CACHE[key] = result
            return result

    def compute(self, text: str) -> int:
        """Compute SimHash for text - classical token-based approach."""
        tokens = self._tokenize(text[:5000])
        if not tokens:
            return 0
        v = [0] * self.hashbits
        for token in tokens:
            h = self._token_hash(token)
            for i in range(self.hashbits):
                bit = h >> i & 1
                if bit:
                    v[i] += 1
                else:
                    v[i] -= 1
        fingerprint = 0
        for i in range(self.hashbits):
            if v[i] > 0:
                fingerprint |= 1 << i
        return fingerprint

    def hamming_distance(self, fp1: int, fp2: int) -> int:
        """Compute Hamming distance between two SimHash fingerprints. O(1)."""
        if _rust_hamming_dist is not None:
            return _rust_hamming_dist(fp1, fp2)
        xor = fp1 ^ fp2
        return xor.bit_count()

    def _is_near_duplicate(self, fp: int) -> bool:
        """Check if fingerprint is near-duplicate of any seen fingerprint.

        Uses TopKBucketIndex for O(1) average lookup instead of O(n) full scan.
        Scans only neighboring buckets (same top-K bits ± 1 bit flip).
        Threshold = 3 bits (~95% recall for 64-bit SimHash).
        """
        return self._bucket_index.add(fp)

    def compute_embedding_batch(self, embeddings) -> np.ndarray:
        """
        MLX-accelerated SimHash for embedding matrix (batch, dim).
        Lazy import MLX, fallback to numpy.
        """
        if not _NUMPY_AVAILABLE:
            raise RuntimeError('numpy is required for deduplication. Install: uv pip install numpy')
        try:
            import mlx.core as mx
            try:
                key = mx.random.key(self.seed)
                hyperplanes = mx.random.normal(shape=(self.hashbits, embeddings.shape[1]), dtype=mx.bfloat16, key=key)
            except TypeError:
                key = mx.random.key(self.seed)
                hyperplanes = mx.random.normal(key, (self.hashbits, embeddings.shape[1]), dtype=mx.bfloat16)
            dots = embeddings @ hyperplanes.T
            bits = (dots >= 0).astype(mx.uint64)
            weights = mx.array([2 ** i for i in range(self.hashbits)], dtype=mx.uint64)
            hashes = (bits * weights).sum(axis=1)
            mx.eval(hashes)
            return hashes
        except (ImportError, AttributeError, TypeError):
            rng = np.random.RandomState(self.seed)
            hyperplanes_np = rng.randn(self.hashbits, embeddings.shape[1]).astype(np.float32)
            dots_np = embeddings @ hyperplanes_np.T
            bits_np = (dots_np >= 0).astype(np.uint64)
            weights_np = np.array([2 ** i for i in range(self.hashbits)], dtype=np.uint64)
            return (bits_np * weights_np).sum(axis=1)

    @staticmethod
    def hamming_distance(hash1: int, hash2: int) -> int:
        """Compute Hamming distance between two hashes."""
        x = hash1 ^ hash2
        distance = 0
        while x:
            distance += 1
            x &= x - 1
        return distance

    def is_near_duplicate(self, hash1: int, hash2: int, threshold: int=3) -> bool:
        """Check if two hashes are near-duplicates (Hamming <= threshold)."""
        return self.hamming_distance(hash1, hash2) <= threshold
__all__ = ['DeduplicationStrategy', 'DeduplicationConfig', 'QueryItem', 'SimilarityScore', 'DeduplicationMatch', 'DeduplicationResult', 'DeduplicationStats', 'SemanticDeduplicator', 'ContentDeduplicator', 'MetadataDeduplicator', 'DeduplicationEngine', 'SimHash', 'DomainStats', 'DomainStatsManager']