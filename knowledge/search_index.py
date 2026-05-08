"""Local BM25 search index with metadata store for OSINT findings."""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SearchDocument:
    """OSINT document for BM25 indexing."""
    url: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0

    def __hash__(self):
        return hash(self.url)


@dataclass
class SearchResult:
    """Search results with timing metadata."""
    query: str
    results: list[SearchDocument]
    timing_ms: float


class BM25Index:
    """BM25 fulltext index over SearchDocument collection.

    Bounded to MAX_BM25_DOCUMENTS to prevent unbounded term_doc_freqs growth.
    Uses rank_bm25 if available, falls back to pure Python implementation.
    """

    MAX_BM25_DOCUMENTS: int = 50000

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._documents: list[SearchDocument] = []
        self._doc_freqs: dict[str, int] = defaultdict(int)
        self._doc_lengths: list[int] = []
        self._avg_doc_length: float = 0.0
        self._term_doc_freqs: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self._N: int = 0
        self._rank_bm25 = None

        # Try to use rank_bm25 for faster search
        try:
            from rank_bm25 import BM25Okapi as _RankBM25
            self._RankBM25 = _RankBM25
            self._use_rank_bm25 = True
        except ImportError:
            self._RankBM25 = None
            self._use_rank_bm25 = False

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenization matching rag_engine pattern."""
        return re.findall(r'\b[a-zA-Z]+\b', text.lower())

    def _score_bm25(
        self, term: str, doc_idx: int, doc_len: int, term_freq: int
    ) -> float:
        """Compute BM25 score for one term-document pair."""
        N = self._N
        df = self._doc_freqs.get(term, 0)
        if df == 0:
            return 0.0

        # IDF formula (standard Robertson-Sparck)
        idf = max(0.0, (N - df + 0.5) / (df + 0.5))
        idf = 1.0 + idf  # Shift to positive

        # TF component with saturation
        tf = term_freq
        numerator = tf * (self.k1 + 1)
        denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / max(self._avg_doc_length, 1))

        return idf * numerator / denominator

    def index(self, documents: list[SearchDocument]) -> None:
        """Index a batch of documents. Silently drops if bound reached."""
        for doc in documents:
            self.add_document(doc)

    def add_document(self, doc: SearchDocument) -> None:
        """Add document to index. Silently drops if MAX reached."""
        if len(self._documents) >= self.MAX_BM25_DOCUMENTS:
            logger.debug("BM25Index at max capacity (%d), dropping document", self.MAX_BM25_DOCUMENTS)
            return

        tokens = self._tokenize(doc.content)
        doc_length = len(tokens)

        self._documents.append(doc)
        self._doc_lengths.append(doc_length)

        term_counts: dict[str, int] = defaultdict(int)
        for token in tokens:
            term_counts[token] += 1

        for term, count in term_counts.items():
            self._doc_freqs[term] += 1
            self._term_doc_freqs[term][len(self._documents) - 1] = count

        self._N = len(self._documents)
        total_len = sum(self._doc_lengths)
        self._avg_doc_length = total_len / self._N if self._N > 0 else 0

        # Reinitialize rank_bm25 if available
        if self._use_rank_bm25 and self._RankBM25 is not None:
            tokenized_corpus = [self._tokenize(d.content) for d in self._documents]
            self._rank_bm25 = self._RankBM25(tokenized_corpus)

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        """Search index, return list of (doc_idx, score) sorted descending."""
        if not self._documents:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        # Use rank_bm25 library if available
        if self._use_rank_bm25 and self._rank_bm25 is not None:
            import numpy as np
            scores = self._rank_bm25.get_scores(query_tokens)
            top_indices = np.argsort(scores)[::-1][:top_k]
            return [(int(idx), float(scores[idx])) for idx in top_indices if scores[idx] > 0]

        # Pure Python fallback
        doc_count = len(self._documents)
        scores: dict[int, float] = defaultdict(float)

        for token in query_tokens:
            if token not in self._term_doc_freqs:
                continue
            for doc_idx, term_freq in self._term_doc_freqs[token].items():
                scores[doc_idx] += self._score_bm25(
                    token, doc_idx, self._doc_lengths[doc_idx], term_freq
                )

        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_scores[:top_k]


class MetadataStore:
    """Dict-backed per-URL metadata store with bulk load support."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, url: str) -> Optional[dict[str, Any]]:
        """Get metadata for URL, returns None if not found."""
        return self._data.get(url)

    def set(self, url: str, metadata: dict[str, Any]) -> None:
        """Set metadata for URL."""
        self._data[url] = metadata

    def delete(self, url: str) -> None:
        """Delete metadata for URL."""
        self._data.pop(url, None)

    def bulk_load(self, entries: dict[str, dict[str, Any]]) -> None:
        """Bulk load URL -> metadata mapping."""
        self._data.update(entries)

    def __len__(self) -> int:
        return len(self._data)


class LocalSearchSeam:
    """Facade combining BM25Index + MetadataStore for local search.

    Provides search(query, top_k) and index(documents) methods.
    Thread-unsafe — single-threaded usage only.
    """

    MAX_RESULT_SET: int = 100  # Hard cap on returned results

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self._bm25 = BM25Index(k1=k1, b=b)
        self._metadata = MetadataStore()

    def index(self, documents: list[SearchDocument]) -> int:
        """Index documents and metadata. Returns count indexed."""
        if not documents:
            return 0

        # Extract metadata
        meta_entries = {}
        for doc in documents:
            meta_entries[doc.url] = doc.metadata

        self._metadata.bulk_load(meta_entries)
        self._bm25.index(documents)
        return len(documents)

    def search(self, query: str, top_k: int = 10) -> SearchResult:
        """Search index, return SearchResult with documents and timing."""
        top_k = min(top_k, self.MAX_RESULT_SET)
        start = perf_counter()
        hits = self._bm25.search(query, top_k=top_k)
        timing_ms = (perf_counter() - start) * 1000

        results = []
        for doc_idx, score in hits:
            doc = self._bm25._documents[doc_idx]
            # Attach metadata
            meta = self._metadata.get(doc.url) or {}
            scored_doc = SearchDocument(
                url=doc.url,
                title=doc.title,
                content=doc.content,
                metadata=meta,
                score=score,
            )
            results.append(scored_doc)

        return SearchResult(query=query, results=results, timing_ms=timing_ms)