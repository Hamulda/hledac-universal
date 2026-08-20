# simhash.py — SimHash domain
"""
[SAFE-3] FFI Circuit Breaker integration for simhash module.

C4 Extension: Added hamming_dist, simhash, is_near_duplicate, SimHashStore
for bit-level near-duplicate detection (5× faster than Jaccard on 10K+ corpus).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from _core._util import aclose

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# C4: Pure-Python SimHashStore fallback (M1 8GB safe)
class _PythonSimHashStore:
    """
    Pure-Python SimHashStore for near-duplicate detection.
    
    O(n) per add_document() call. For < 100k documents per store.
    For larger scale: partition by bucket or use LSH index.
    
    Args:
        threshold: Max Hamming distance for near-duplicate (default: 3)
        ngram_size: Tokenization granularity (default: 2)
    """
    
    __slots__ = ('_fingerprints', '_threshold', '_ngram_size')
    
    def __init__(self, threshold: int = 3, ngram_size: int = 2) -> None:
        self._fingerprints: list[tuple[int, str]] = []
        self._threshold = threshold
        self._ngram_size = ngram_size
    
    @property
    def threshold(self) -> int:
        return self._threshold
    
    @property
    def ngram_size(self) -> int:
        return self._ngram_size
    
    def compute(self, text: str) -> int:
        """Compute fingerprint for text using stored ngram_size."""
        return _python_compute_simhash(text, self._ngram_size)
    
    def add_document(self, text: str, doc_id: str) -> tuple[bool, str | None]:
        """
        Add document to store, returns near-duplicate detection result.
        
        Returns:
            (is_new: bool, nearest_duplicate_id: str | None)
        """
        fp = _python_compute_simhash(text, self._ngram_size)
        
        # Search for near-duplicate
        for existing_fp, existing_id in self._fingerprints:
            dist = _python_hamming_dist(fp, existing_fp)
            if dist <= self._threshold:
                return (False, existing_id)
        
        self._fingerprints.append((fp, doc_id))
        return (True, None)
    
    def fingerprint_for(self, text: str) -> int:
        """Get fingerprint without adding to store using stored ngram_size."""
        return _python_compute_simhash(text, self._ngram_size)
    
    def __len__(self) -> int:
        return len(self._fingerprints)
    
    def __getstate__(self) -> tuple:
        return (self._fingerprints, self._threshold, self._ngram_size)
    
    def __setstate__(self, state: tuple) -> None:
        self._fingerprints = state[0]
        self._threshold = state[1]
        self._ngram_size = state[2]


def _python_hamming_dist(a: int, b: int) -> int:
    """Compute Hamming distance between two integers."""
    return (a ^ b).bit_count()


def _python_find_near_duplicates(fingerprints: list[int], threshold: int = 3) -> list[tuple[int, int]]:
    """Pure-Python fallback: find near-duplicate fingerprint pairs.

    G4 Extension: O(n²) brute-force. Threshold = max Hamming distance for near-duplicate.
    """
    n = len(fingerprints)
    results: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            dist = _python_hamming_dist(fingerprints[i], fingerprints[j])
            if dist <= threshold:
                results.append((i, j))
    return results


def _python_is_near_duplicate(text_a: str, text_b: str, threshold: int = 3, ngram_size: int = 2) -> bool:
    """Pure-Python is_near_duplicate check."""
    fp_a = _python_compute_simhash(text_a, ngram_size)
    fp_b = _python_compute_simhash(text_b, ngram_size)
    return _python_hamming_dist(fp_a, fp_b) <= threshold

# [SAFE-3] FFI Circuit Breaker
try:
    from hledac.universal._core.ffi_circuit_breaker import (
        FFI_MODULE_SIMHASH,
        get_ffi_circuit_breaker,
    )
    _FFI_CB_AVAILABLE = True
except ImportError:
    _FFI_CB_AVAILABLE = False
    FFI_MODULE_SIMHASH = "simhash"


class _RustSimhashDomain:
    """
    [SAFE-3] Rust SimHash domain with FFI circuit breaker.
    
    C4 Extension: Exposes hamming_dist, simhash, is_near_duplicate, SimHashStore
    for bit-level near-duplicate detection.
    """
    __slots__ = ("_ext", "_ffi_cb")

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext
        # [SAFE-3] Initialize FFI circuit breaker
        self._ffi_cb = get_ffi_circuit_breaker() if _FFI_CB_AVAILABLE else None

    # C4: Added hamming_dist - computes Hamming distance between two fingerprints
    def hamming_dist(self, a: int, b: int) -> int:
        """
        Compute Hamming distance between two 64-bit fingerprints.
        
        Args:
            a: First fingerprint
            b: Second fingerprint
            
        Returns:
            Hamming distance (0 = identical, 64 = opposite)
        """
        if self._ffi_cb is not None:
            def rust_call() -> int:
                return self._ext.hamming_dist(a, b)
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMHASH, rust_call, (a, b)
            )
            if result.success:
                return result.value  # type: ignore[return-value]
            return _python_hamming_dist(a, b)
        return self._ext.hamming_dist(a, b)

    # C4: Added simhash - alias for compute_simhash with explicit naming
    def simhash(self, text: str, ngram_size: int = 2) -> int:
        """
        Compute SimHash fingerprint for text.
        
        Args:
            text: Input text to hash
            ngram_size: Tokenization granularity (1=words, 2+=char n-grams)
            
        Returns:
            64-bit fingerprint (deterministic)
        """
        if self._ffi_cb is not None:
            def rust_call() -> int:
                return self._ext.simhash(text, ngram_size)
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMHASH, rust_call, (text, ngram_size)
            )
            if result.success:
                return result.value  # type: ignore[return-value]
            return _python_compute_simhash(text)
        return self._ext.simhash(text, ngram_size)

    # C4: Added is_near_duplicate - checks if two texts are near-duplicates
    def is_near_duplicate(
        self, text_a: str, text_b: str, threshold: int = 3, ngram_size: int = 2
    ) -> bool:
        """
        Check if two texts are near-duplicates.
        
        Args:
            text_a: First text
            text_b: Second text
            threshold: Max Hamming distance for "same" (default: 3)
            ngram_size: Tokenization granularity
            
        Returns:
            True if near-duplicate (Hamming distance <= threshold)
        """
        if self._ffi_cb is not None:
            def rust_call() -> bool:
                return self._ext.is_near_duplicate(text_a, text_b, threshold, ngram_size)
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMHASH, rust_call, (text_a, text_b, threshold, ngram_size)
            )
            if result.success:
                return result.value  # type: ignore[return-value]
            return _python_is_near_duplicate(text_a, text_b, threshold, ngram_size)
        return self._ext.is_near_duplicate(text_a, text_b, threshold, ngram_size)

    # C4: Added SimHashStore - near-duplicate store for document deduplication
    def SimHashStore(self, threshold: int = 3, ngram_size: int = 2) -> Any:
        """
        Create a near-duplicate store for document deduplication.
        
        Args:
            threshold: Max Hamming distance for near-duplicate (default: 3)
            ngram_size: Tokenization granularity (default: 2)
            
        Returns:
            SimHashStore instance (Rust or Python fallback)
        """
        if self._ffi_cb is not None:
            # Try Rust SimHashStore first
            def rust_call() -> Any:
                return self._ext.SimHashStore(threshold, ngram_size)
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMHASH, rust_call, (threshold, ngram_size)
            )
            if result.success:
                return result.value  # type: ignore[return-value]
            return _PythonSimHashStore(threshold, ngram_size)
        
        # Direct Rust call without FFI CB
        try:
            return self._ext.SimHashStore(threshold, ngram_size)
        except AttributeError:
            return _PythonSimHashStore(threshold, ngram_size)

    def compute_simhash(self, text: str) -> int:
        """[SAFE-3] Compute simhash with circuit breaker."""
        if self._ffi_cb is not None:
            def rust_call() -> int:
                return self._ext.compute_simhash(text)
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMHASH, rust_call, text
    )
            if result.success:
                return result.value  # type: ignore[return-value]
            return _python_compute_simhash(text)
        return self._ext.compute_simhash(text)

    def batch_compute_simhash(self, texts: list[str]) -> list[int]:
        """[SAFE-3] Batch compute simhash with circuit breaker."""
        if self._ffi_cb is not None:
            def rust_call() -> list[int]:
                return self._ext.batch_compute_simhash(texts)
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMHASH, rust_call, texts
    )
            if result.success:
                return result.value  # type: ignore[return-value]
            return [_python_compute_simhash(t) for t in texts]
        return self._ext.batch_compute_simhash(texts)

    def find_near_duplicates(self, fingerprints: list[int], threshold: int = 3) -> list[tuple[int, int]]:
        """[SAFE-3] Find near-duplicate pairs from pre-computed fingerprints.

        G4 Extension: Added find_near_duplicates for batch near-duplicate detection.
        O(n²) brute-force over fingerprints. Used by semantic_deduplicator.py.
        """
        if self._ffi_cb is not None:
            def rust_call() -> list[tuple[int, int]]:
                return self._ext.find_near_duplicates(fingerprints, threshold)
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMHASH, rust_call, (fingerprints, threshold)
            )
            if result.success:
                return result.value  # type: ignore[return-value]
            return _python_find_near_duplicates(fingerprints, threshold)
        return self._ext.find_near_duplicates(fingerprints, threshold)


class _PythonSimhashDomain:
    """
    Pure-Python SimHash fallback.
    
    C4 Extension: Exposes hamming_dist, simhash, is_near_duplicate, SimHashStore
    for bit-level near-duplicate detection.
    """

    __slots__ = ()

    @staticmethod
    def compute_simhash(text: str, ngram_size: int = 2) -> int:
        return _python_compute_simhash(text, ngram_size)

    @staticmethod
    def batch_compute_simhash(texts: list[str], ngram_size: int = 2) -> list[int]:
        return [_python_compute_simhash(t, ngram_size) for t in texts]
    
    # C4: Added hamming_dist
    @staticmethod
    def hamming_dist(a: int, b: int) -> int:
        return _python_hamming_dist(a, b)
    
    # C4: Added simhash
    @staticmethod
    def simhash(text: str, ngram_size: int = 2) -> int:
        return _python_compute_simhash(text, ngram_size)
    
    # C4: Added is_near_duplicate
    @staticmethod
    def is_near_duplicate(
        text_a: str, text_b: str, threshold: int = 3, ngram_size: int = 2
    ) -> bool:
        return _python_is_near_duplicate(text_a, text_b, threshold, ngram_size)
    
    # C4: Added SimHashStore
    def SimHashStore(self, threshold: int = 3, ngram_size: int = 2) -> _PythonSimHashStore:
        return _PythonSimHashStore(threshold, ngram_size)

    # G4: Added find_near_duplicates
    @staticmethod
    def find_near_duplicates(fingerprints: list[int], threshold: int = 3) -> list[tuple[int, int]]:
        return _python_find_near_duplicates(fingerprints, threshold)


def _fnv64_hash(s: str) -> int:
    """
    Pure-Python FNV-1a 64-bit hash function.
    
    Matches the Rust fnv64() implementation for cross-language consistency.
    """
    FNV_PRIME = 1099511628211
    FNV_OFFSET = 14695981039346656037
    h = FNV_OFFSET
    for byte in s.encode('utf-8'):
        h ^= byte
        h = (h * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF  # 64-bit wrap
    return h


def _python_compute_simhash(text: str, ngram_size: int = 2) -> int:
    """
    Pure-Python SimHash approximation.

    MUST match Rust tokenize() and compute_simhash_from_tokens() exactly:
    - ngram_size <= 1: word tokenization (filter words with len <= 2)
    - ngram_size > 1: character n-grams (sliding window)
    - Uses FNV-1a 64-bit hash (same as Rust)
    - Term frequency weighting (same as Rust)
    """
    if not text:
        return 0

    # Normalize text: keep only alphanumeric and whitespace (matches Rust)
    clean = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in text).lower()

    if ngram_size <= 1:
        # Word tokenization - filter short words (len > 2) like Rust
        tokens = [w for w in clean.split() if len(w) > 2]
    else:
        # Character n-grams (matches Rust: chars.windows(ngram_size))
        chars = list(clean)
        tokens = [''.join(chars[i:i + ngram_size]) for i in range(len(chars) - ngram_size + 1)]

    if not tokens:
        return 0

    # Compute term frequency weights (matches Rust compute_tf_weights)
    freq: dict[str, float] = {}
    total = len(tokens)
    for token in tokens:
        freq[token] = freq.get(token, 0) + 1.0

    # Sort tokens for deterministic iteration order (matches Rust)
    sorted_tokens = sorted(freq.keys())

    # Accumulate weighted feature vectors
    v = [0.0] * 64
    for token in sorted_tokens:
        weight = freq[token] / total
        h = _fnv64_hash(token)
        for i in range(64):
            if (h >> i) & 1:
                v[i] += weight
            else:
                v[i] -= weight

    # Final fingerprint
    result = 0
    for i in range(64):
        if v[i] > 0:
            result |= 1 << i
    return result


def get_domain(ext: object | None) -> _RustSimhashDomain | _PythonSimhashDomain:
    if ext is not None:
        return _RustSimhashDomain(ext)
    return _PythonSimhashDomain()
