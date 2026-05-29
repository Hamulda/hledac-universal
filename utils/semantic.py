"""
SemanticFilter - The Filter
============================

Memory-efficient semantic filtering using ModernBERT (MLX).

Features:
    - ModernBERT 768-dim embeddings via MLX
    - Fast similarity computation
    - Token-efficient for M1 Silicon (8GB RAM)
    - Pre-processing filter before Context Manager
    - NO sentence-transformers dependency

Integration:
    - Placed BEFORE Context Manager
    - Web data does NOT go to DeepSeek until passing this filter
    - Saves tokens by filtering irrelevant content early

Usage:
    filter = SemanticFilter()
    result = filter.filter(content, query, threshnew=0.7)
    if result.passed:
        # Send to DeepSeek
    else:
        # Skip, save tokens
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    """Result of semantic filtering."""
    passed: bool
    similarity: float
    filtered_content: str | None
    metadata: dict[str, Any] | None = None


class LightweightTokenizer:
    """
    Lightweight tokenizer for fast text processing.

    Uses simple whitespace and punctuation tokenization
    for M1 Silicon memory efficiency.
    """

    def __init__(self, use_bigrams: bool = False):
        """Initialize LightweightTokenizer."""
        self._pattern = re.compile(r'\b\w{3,}\b')
        self._use_bigrams = use_bigrams

    def tokenize(self, text: str) -> list[str]:
        """
        Tokenize text into words.

        Args:
            text: Text to tokenize

        Returns:
            List of tokens
        """
        words = self._pattern.findall(text.lower())

        if self._use_bigrams and len(words) > 1:
            bigrams = []
            for i in range(len(words) - 1):
                bigrams.append(f"{words[i]}_{words[i+1]}")
            words.extend(bigrams)

        return words

    def extract_keywords(self, text: str, top_k: int = 10) -> list[str]:
        """
        Extract top keywords from text.

        Args:
            text: Text to extract keywords from
            top_k: Number of keywords to return

        Returns:
            List of top keywords
        """
        tokens = self.tokenize(text)

        from collections import Counter
        counter = Counter(tokens)

        return [token for token, _ in counter.most_common(top_k)]


class ModernBERTEmbedding:
    """
    ModernBERT-based embedding for semantic filtering.

    Uses ModernBERT via MLX for 768-dimensional embeddings.
    Optimized for M1 Silicon (8GB RAM).

    REPLACES: Model2VecEmbedding, SentenceTransformerEmbedding
    """

    EMBEDDING_DIM = 768
    DEFAULT_MODEL = "mlx-community/answerdotai-ModernBERT-base-6bit"

    def __init__(self, model_path: str | None = None):
        """
        Initialize ModernBERTEmbedding.

        Args:
            model_path: Optional custom model path (default: 6bit ModernBERT)
        """
        self._model_path = model_path or self.DEFAULT_MODEL
        self._embedder: Any | None = None
        self._initialized = False

        try:
            self._load_model()
            logger.info("[EMBED] Using ModernBERT MLX (768d)")
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize ModernBERT: {e}")
            self._initialized = False
            raise

    def _load_model(self):
        """Load ModernBERT embedder."""
        from ...embeddings.modernbert_embedder import ModernBERTEmbedder

        logger.info(f"[MODEL LOAD] ModernBERT: {self._model_path} (MLX) dim=768")
        self._embedder = ModernBERTEmbedder(
            model_path=self._model_path,
            lazy_load=True,
            normalize=True
        )

    def encode(self, text: str) -> list[float]:
        """
        Encode text to embedding vector.

        Args:
            text: Text to encode

        Returns:
            Embedding vector (768 dimensions)
        """
        if not self._initialized or self._embedder is None:
            raise RuntimeError("ModernBERT not initialized")

        try:
            result = self._embedder.embed(text)
            return result.embedding.tolist()
        except Exception as e:
            logger.error(f"Failed to encode with ModernBERT: {e}")
            # Return zero vector as fallback
            return [0.0] * self.EMBEDDING_DIM

    def cosine_similarity(
        self,
        vec1: list[float],
        vec2: list[float]
    ) -> float:
        """
        Compute cosine similarity between two vectors.

        Args:
            vec1: First vector
            vec2: Second vector

        Returns:
            Cosine similarity (-1 to 1)
        """
        if not vec1 or not vec2:
            return 0.0

        # Convert to numpy arrays
        a = np.array(vec1)
        b = np.array(vec2)

        # Handle dimension mismatch (pad or truncate)
        if len(a) != len(b):
            min_len = min(len(a), len(b))
            a = a[:min_len]
            b = b[:min_len]

        norm1 = np.linalg.norm(a)
        norm2 = np.linalg.norm(b)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(np.dot(a, b) / (norm1 * norm2))

    def unload(self) -> None:
        """Unload model from memory."""
        if self._embedder is not None:
            self._embedder.unload()
            self._embedder = None
            self._initialized = False
            logger.info("ModernBERT embedding unloaded")


class SimpleEmbedding:
    """
    Simple word embedding using TF-IDF style weighting.

    Fallback when ModernBERT is not available.
    Memory-efficient for M1 Silicon.

    DEPRECATED: Use ModernBERTEmbedding instead.
    """

    def __init__(self):
        """Initialize SimpleEmbedding."""
        self._vocabulary: dict[str, int] = {}
        self._idf_cache: dict[str, float] = {}
        self._doc_count = 0
        self._initialized = False
        self._docs_for_idf: list[set] = []
        self._tokenizer = LightweightTokenizer(use_bigrams=False)

    def fit(self, documents: list[str]):
        """
        Build vocabulary from documents.

        Args:
            documents: List of documents
        """
        tokenizer = LightweightTokenizer()

        for doc in documents:
            tokens = set(tokenizer.tokenize(doc))
            self._docs_for_idf.append(tokens)
            for token in tokens:
                self._idf_cache[token] = self._idf_cache.get(token, 0) + 1

            self._doc_count += 1

        self._vocabulary = {token: idx for idx, token in enumerate(self._idf_cache.keys())}

        for token in self._idf_cache:
            self._idf_cache[token] = self._doc_count / self._idf_cache[token]

        self._initialized = True

    def encode(self, text: str) -> list[float]:
        """
        Encode text to embedding vector.

        Args:
            text: Text to encode

        Returns:
            Embedding vector
        """
        if not self._initialized:
            init_text = " ".join([
                "machine learning neural networks artificial intelligence deep learning algorithms techniques",
                "programming coding software development computer science technology digital",
                "data analysis statistics optimization techniques models training",
                "web development internet research academic papers scientific studies",
                "advanced techniques neural networks machine learning artificial intelligence",
                "relevant article discussing advanced machine learning neural networks"
            ])
            self.fit([init_text])

        tokens = self._tokenizer.tokenize(text)

        tf = {}
        for token in tokens:
            tf[token] = tf.get(token, 0) + 1

        vector = [0.0] * len(self._vocabulary)

        for token, count in tf.items():
            if token in self._vocabulary:
                idx = self._vocabulary[token]
                tfidf = count * self._idf_cache.get(token, 1.0)
                vector[idx] = tfidf
            else:
                idx = len(self._vocabulary)
                self._vocabulary[token] = idx
                self._idf_cache[token] = 1.0
                tfidf = count * 1.0
                vector.append(tfidf)

        norm = sum(x ** 2 for x in vector) ** 0.5
        if norm > 0:
            vector = [x / norm for x in vector]

        return vector

    def cosine_similarity(
        self,
        vec1: list[float],
        vec2: list[float]
    ) -> float:
        """
        Compute cosine similarity between two vectors.

        Args:
            vec1: First vector
            vec2: Second vector

        Returns:
            Cosine similarity (-1 to 1)
        """
        if len(vec1) != len(vec2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2, strict=False))
        norm1 = sum(a ** 2 for a in vec1) ** 0.5
        norm2 = sum(b ** 2 for b in vec2) ** 0.5

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)


class SemanticFilter:
    """
    Semantic filter for content relevance checking.

    Uses ModernBERT for fast, memory-efficient similarity computation.
    Filters content before it reaches DeepSeek to save tokens.

    Usage context:
        - Placed BEFORE Context Manager
        - Web data does NOT go to DeepSeek until passing this filter
        - Saves tokens by filtering irrelevant content early

    Example:
        filter = SemanticFilter()
        result = filter.filter(
            content="Python is a great programming language",
            query="best programming languages",
            threshnew=0.7
        )
        if result.passed:
            # Send to DeepSeek
    """

    def __init__(
        self,
        threshnew: float = 0.7,
        use_fallback: bool = False  # Changed default - no fallback
    ):
        """
        Initialize SemanticFilter.

        Args:
            threshnew: Default similarity threshnew (0-1)
            use_fallback: Whether to use fallback if ModernBERT unavailable
        """
        self.threshnew = threshnew
        self._embedding: Any = None
        self._tokenizer = LightweightTokenizer()
        self._use_fallback = use_fallback
        self._embedding_type = "none"

        self._init_embedding()

    def _init_embedding(self):
        """Initialize embedding model."""
        logger.info("[EMBED] Initializing ModernBERT MLX (768d) as primary solution")
        try:
            self._embedding = ModernBERTEmbedding()

            test_vec = self._embedding.encode("test")
            if not test_vec or len(test_vec) == 0:
                raise RuntimeError("ModernBERT returned empty vector")

            if len(test_vec) != ModernBERTEmbedding.EMBEDDING_DIM:
                raise RuntimeError(f"Expected dim={ModernBERTEmbedding.EMBEDDING_DIM}, got {len(test_vec)}")

            logger.info(f"[EMBED] ModernBERT initialized successfully, vector size: {len(test_vec)}")
            self._embedding_type = "modernbert"

        except Exception as e:
            logger.error(f"[EMBED] Failed to initialize ModernBERT: {e}")
            if self._use_fallback:
                logger.warning("[EMBED] Falling back to SimpleEmbedding (DEPRECATED)")
                self._embedding = SimpleEmbedding()
                self._embedding.fit(["test query", "test document"])
                self._embedding_type = "simple"
            else:
                logger.error("[EMBED] ModernBERT initialization failed and fallback disabled")
                raise RuntimeError(f"ModernBERT initialization failed: {e}")

    def compute_similarity(
        self,
        text1: str,
        text2: str
    ) -> float:
        """
        Compute semantic similarity between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score (0-1)
        """
        try:
            vec1 = self._embedding.encode(text1)
            vec2 = self._embedding.encode(text2)

            logger.debug(f"Vec1 length: {len(vec1)}, Vec2 length: {len(vec2)}")

            if not vec1 or not vec2:
                return 0.0

            similarity = self._embedding.cosine_similarity(vec1, vec2)

            tokens1 = self._tokenizer.tokenize(text1)
            tokens2 = self._tokenizer.tokenize(text2)
            common_tokens = set(tokens1) & set(tokens2)

            if len(tokens2) > 0:
                overlap_ratio = len(common_tokens) / len(tokens2)
                similarity = similarity / (1 - 0.3 * overlap_ratio)

            logger.debug(f"Similarity: {similarity}, Overlap ratio: {overlap_ratio if len(tokens2) > 0 else 0}")

            return max(0.0, min(1.0, similarity))

        except Exception as e:
            logger.error(f"Failed to compute similarity: {e}")
            return 0.0

    def filter(
        self,
        content: str,
        query: str,
        threshnew: float | None = None
    ) -> FilterResult:
        """
        Filter content based on semantic similarity to query.

        Args:
            content: Content to filter
            query: Query to match against
            threshnew: Optional custom threshnew

        Returns:
            FilterResult with filtering result
        """
        if threshnew is None:
            threshnew = self.threshnew

        similarity = self.compute_similarity(content, query)
        passed = similarity >= threshnew

        return FilterResult(
            passed=passed,
            similarity=similarity,
            filtered_content=content if passed else None,
            metadata={
                'threshnew': threshnew,
                'content_length': len(content),
                'query_length': len(query),
                'embedding_type': self._embedding_type
            }
        )

    def filter_batch(
        self,
        contents: list[str],
        query: str,
        threshnew: float | None = None
    ) -> list[FilterResult]:
        """
        Filter multiple contents against a query.

        Args:
            contents: List of contents to filter
            query: Query to match against
            threshnew: Optional custom threshnew

        Returns:
            List of FilterResults
        """
        if threshnew is None:
            threshnew = self.threshnew

        query_vec = self._embedding.encode(query)
        results = []

        for content in contents:
            content_vec = self._embedding.encode(content)

            if not query_vec or not content_vec:
                similarity = 0.0
            else:
                similarity = self._embedding.cosine_similarity(query_vec, content_vec)

            passed = similarity >= threshnew

            results.append(FilterResult(
                passed=passed,
                similarity=similarity,
                filtered_content=content if passed else None,
                metadata={
                    'threshnew': threshnew,
                    'content_length': len(content),
                    'embedding_type': self._embedding_type
                }
            ))

        return results

    def extract_relevant_snippets(
        self,
        content: str,
        query: str,
        max_snippets: int = 3,
        snippet_length: int = 200
    ) -> list[str]:
        """
        Extract most relevant snippets from content.

        Args:
            content: Content to extract snippets from
            query: Query to match against
            max_snippets: Maximum number of snippets to return
            snippet_length: Maximum length of each snippet

        Returns:
            List of relevant snippets
        """
        sentences = re.split(r'[.!?]+', content)

        sentence_scores = []
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 10:
                continue

            similarity = self.compute_similarity(sentence, query)
            sentence_scores.append((similarity, sentence))

        sentence_scores.sort(reverse=True, key=lambda x: x[0])

        snippets = []
        for score, sentence in sentence_scores[:max_snippets]:
            if score < self.threshnew:
                break

            snippet = sentence[:snippet_length]
            snippets.append(snippet)

        return snippets

    def unload(self) -> None:
        """Unload embedding model from memory."""
        if hasattr(self._embedding, 'unload'):
            self._embedding.unload()
            logger.info("SemanticFilter embedding unloaded")


class KeywordFilter:
    """
    Simple keyword-based filter for fast pre-filtering.

    Used as a first-pass filter before semantic filtering
    to save computational resources.
    """

    def __init__(self):
        """Initialize KeywordFilter."""
        self._tokenizer = LightweightTokenizer()

    def contains_keywords(
        self,
        content: str,
        keywords: list[str],
        min_matches: int = 1
    ) -> bool:
        """
        Check if content contains minimum number of keywords.

        Args:
            content: Content to check
            keywords: List of keywords to look for
            min_matches: Minimum number of keyword matches

        Returns:
            True if enough keywords found
        """
        content_lower = content.lower()
        matches = 0

        for keyword in keywords:
            if keyword.lower() in content_lower:
                matches += 1

            if matches >= min_matches:
                return True

        return False

    def extract_matching_keywords(
        self,
        content: str,
        keywords: list[str]
    ) -> list[str]:
        """
        Extract keywords that appear in content.

        Args:
            content: Content to extract from
            keywords: List of keywords to check

        Returns:
            List of matching keywords
        """
        content_lower = content.lower()
        matches = []

        for keyword in keywords:
            if keyword.lower() in content_lower:
                matches.append(keyword)

        return matches


# DEPRECATED CLASSES - Kept for backwards compatibility but not used

class Model2VecEmbedding:
    """
    DEPRECATED: Use ModernBERTEmbedding instead.

    Model2Vec-based embedding for efficient semantic filtering.
    """

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "Model2VecEmbedding is DEPRECATED. Use ModernBERTEmbedding from "
            "hledac.embeddings.modernbert_embedder directly."
        )


class SentenceTransformerEmbedding:
    """
    DEPRECATED: Use ModernBERTEmbedding instead.

    SentenceTransformer-based embedding for semantic filtering.
    """

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "SentenceTransformerEmbedding is DEPRECATED. Use ModernBERTEmbedding "
            "which uses MLX-optimized ModernBERT (768d). "
            "NO sentence-transformers dependency required."
        )
