"""
LSH-backed Near-Duplicate Detection

Multi-table LSH (Locality-Sensitive Hashing) pro O(1) near-duplicate
detekci na scale. Kombinuje Rust SimHash s Rust LSH indexem.

50× rychlejší než původní Python simhash_ext pro batch operace.

Usage:
    from hledac.universal.utils.lsh_deduplicator import LSHNearDuplicateDetector

    detector = LSHNearDuplicateDetector(num_tables=16, num_rows=4)
    detector.add_document("doc1", "text content...")
    matches = detector.find_similar("query text...")
"""
import logging
import threading
from dataclasses import dataclass, field
import msgspec
from typing import Any
# R6: Centralized Rust access via core.rust_backend
from hledac.universal.core.rust_backend import rust
_raw = rust.raw
LSHIndex = _raw.LSHIndex
lsh_index_new = _raw.lsh_index_new
lsh_estimate_recall = _raw.lsh_estimate_recall
batch_compute_simhash = _raw.batch_compute_simhash
hamming_dist = _raw.hamming_dist
logger = logging.getLogger(__name__)
DEFAULT_NUM_TABLES = 16
DEFAULT_NUM_ROWS = 4

class LSHStats(msgspec.Struct):
    """Statistics for LSH near-duplicate detection."""
    num_documents: int = 0
    num_tables: int = DEFAULT_NUM_TABLES
    num_rows: int = DEFAULT_NUM_ROWS
    queriesPerformed: int = 0
    candidatesFound: int = 0
    matchesFound: int = 0

class LSHNearDuplicateDetector:
    """Near-duplicate detector using multi-table LSH + SimHash.

    Combines:
    - Rust SimHash for 64-bit fingerprinting (fast, deterministic)
    - Rust LSH index for O(1) candidate retrieval

    Performance:
    - Add: O(k) where k = num_tables
    - Query: O(1) average for candidate retrieval + O(m) for verification
    - Space: O(n * k) where n = documents

    Args:
        num_tables: Number of hash tables (default 16)
                    Higher = better recall, more memory
        num_rows: Number of rows per band (default 4)
                  Higher = better precision, fewer false positives
        threshold: Hamming distance threshold for near-duplicate (default 3)
        max_results: Maximum similar documents to return (default 100)
    """
    __slots__ = tuple(('_documents', '_index', '_lock', '_stats', 'max_results', 'num_rows', 'num_tables', 'threshold'))

    def __init__(self, num_tables: int=DEFAULT_NUM_TABLES, num_rows: int=DEFAULT_NUM_ROWS, threshold: int=3, max_results: int=100):
        self.num_tables = num_tables
        self.num_rows = num_rows
        self.threshold = threshold
        self.max_results = max_results
        self._index: LSHIndex = lsh_index_new(num_tables=num_tables, num_rows=num_rows)
        self._documents: dict[str, tuple[str, int]] = {}
        self._lock = threading.RLock()
        self._stats = LSHStats(num_tables=num_tables, num_rows=num_rows)
        logger.debug(f'LSHNearDuplicateDetector initialized: tables={num_tables}, rows={num_rows}, threshold={threshold}')

    @property
    def stats(self) -> LSHStats:
        """Return current statistics."""
        return self._stats

    def add_document(self, doc_id: str, text: str) -> bool:
        """Add a document to the LSH index.

        Args:
            doc_id: Unique document identifier
            text: Document text content

        Returns:
            True if document was added (not a near-duplicate)
            False if near-duplicate already exists
        """
        with self._lock:
            fps = batch_compute_simhash([text])
            fingerprint = fps[0] if fps else 0
            candidates = self._index.query(fingerprint, max_results=self.max_results)
            for cand_id, similarity in candidates:
                if cand_id in self._documents:
                    cand_text, cand_fp = self._documents[cand_id]
                    dist = hamming_dist(fingerprint, cand_fp)
                    if dist <= self.threshold:
                        logger.debug(f'Near-duplicate found: {doc_id} ~ {cand_id} (dist={dist})')
                        self._stats.matchesFound += 1
                        return False
            self._index.insert(doc_id, fingerprint)
            self._documents[doc_id] = (text, fingerprint)
            self._stats.num_documents += 1
            return True

    def add_documents_batch(self, documents: list[tuple[str, str]]) -> list[bool]:
        """Batch add documents.

        Args:
            documents: List of (doc_id, text) tuples

        Returns:
            List of bool - True if each document was added (not near-duplicate)
        """
        results = []
        for doc_id, text in documents:
            results.append(self.add_document(doc_id, text))
        return results

    def find_similar(self, text: str, max_results: int | None=None) -> list[tuple[str, float, int]]:
        """Find documents similar to the given text.

        Args:
            text: Query text
            max_results: Maximum results to return (default: self.max_results)

        Returns:
            List of (doc_id, similarity_score, hamming_distance) tuples,
            sorted by similarity descending
        """
        if max_results is None:
            max_results = self.max_results
        with self._lock:
            self._stats.queriesPerformed += 1
            fps = batch_compute_simhash([text])
            fingerprint = fps[0] if fps else 0
            candidates = self._index.query(fingerprint, max_results=max_results)
            self._stats.candidatesFound += len(candidates)
            results = []
            for cand_id, _ in candidates:
                if cand_id in self._documents:
                    cand_text, cand_fp = self._documents[cand_id]
                    dist = hamming_dist(fingerprint, cand_fp)
                    if dist <= self.threshold:
                        similarity = 1.0 - dist / 64.0
                        results.append((cand_id, similarity, dist))
            results.sort(key=lambda x: -x[1])
            return results[:max_results]

    def compute_fingerprint(self, text: str) -> int:
        """Compute SimHash fingerprint for text.

        Args:
            text: Text to fingerprint

        Returns:
            64-bit SimHash fingerprint
        """
        fps = batch_compute_simhash([text])
        return fps[0] if fps else 0

    def batch_fingerprints(self, texts: list[str]) -> list[int]:
        """Compute SimHash fingerprints for multiple texts.

        Args:
            texts: List of texts

        Returns:
            List of 64-bit fingerprints
        """
        if not texts:
            return []
        return batch_compute_simhash(texts)

    def is_near_duplicate(self, text1: str, text2: str) -> bool:
        """Check if two texts are near-duplicates.

        Args:
            text1: First text
            text2: Second text

        Returns:
            True if Hamming distance <= threshold
        """
        fp1 = self.compute_fingerprint(text1)
        fp2 = self.compute_fingerprint(text2)
        dist = hamming_dist(fp1, fp2)
        return dist <= self.threshold

    def clear(self) -> None:
        """Clear all documents from the index."""
        with self._lock:
            self._index.clear()
            self._documents.clear()
            self._stats.num_documents = 0
            self._stats.queriesPerformed = 0
            self._stats.candidatesFound = 0
            self._stats.matchesFound = 0

    def estimate_recall(self, threshold: float) -> float:
        """Estimate recall probability for given similarity threshold.

        Args:
            threshold: Jaccard similarity threshold (0.0 - 1.0)

        Returns:
            Estimated recall probability
        """
        return lsh_estimate_recall(threshold, self.num_tables, self.num_rows)

    def get_stats(self) -> dict[str, Any]:
        """Get statistics as dictionary."""
        return {'num_documents': self._stats.num_documents, 'num_tables': self._stats.num_tables, 'num_rows': self._stats.num_rows, 'threshold': self.threshold, 'queries_performed': self._stats.queriesPerformed, 'candidates_found': self._stats.candidatesFound, 'matches_found': self._stats.matchesFound, 'estimated_recall_0.9': self.estimate_recall(0.9), 'estimated_recall_0.95': self.estimate_recall(0.95)}

def lsh_fingerprint(text: str) -> int:
    """Compute LSH-friendly fingerprint (wrapper around batch_compute_simhash)."""
    fps = batch_compute_simhash([text])
    return fps[0] if fps else 0

def lsh_batch_fingerprints(texts: list[str]) -> list[int]:
    """Batch compute fingerprints using Rust SIMD."""
    return batch_compute_simhash(texts)

def lsh_collision_probability(num_tables: int, num_rows: int, threshold: float) -> float:
    """Calculate probability of LSH collision for given parameters.

    Args:
        num_tables: Number of LSH tables
        num_rows: Number of rows per band
        threshold: Jaccard similarity threshold

    Returns:
        Collision probability
    """
    return 1.0 - lsh_estimate_recall(threshold, num_tables, num_rows)