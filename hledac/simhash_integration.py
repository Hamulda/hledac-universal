"""
SimHash Integration Module
=========================

Python facade for Rust SimHash extension with knowledge store integration.

USAGE:
    from hledac.simhash_integration import NearDuplicateDetector

    detector = NearDuplicateDetector()
    is_new, duplicate_id = detector.check_and_add("content", "finding-id-1")

API:
    NearDuplicateDetector(threshold=3, ngram_size=2)
        check_and_add(text, doc_id) -> (is_new: bool, duplicate_id: str | None)
        fingerprint(text) -> int
        len() -> int
        save_state() / load_state(path) for persistence

PERSISTENCE:
    State saved via pickle (SimHashStore.__getstate__/__setstate__)
    Store as LMDB or file for cross-sprint persistence.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# --- Rust extension imports with fallback ---
_SIMHASH_AVAILABLE = False
_rust_ext: object | None = None

try:
    import hledac_rust_extensions
    _SIMHASH_AVAILABLE = True
    _rust_ext = hledac_rust_extensions
except ImportError:
    logger.debug("hledac_rust_extensions not available - SimHash disabled")


# Type for SimHashStore (for type checking)
if _SIMHASH_AVAILABLE:
    SimHashStore = getattr(_rust_ext, "SimHashStore", None)  # type: ignore[assignment, misc]
else:
    SimHashStore = None  # type: ignore[assignment, misc]


# ---------------------------------------------------------------------------
# Near-Duplicate Detector (main API)
# ---------------------------------------------------------------------------

class NearDuplicateDetector:
    """
    Near-duplicate detection using SimHash with Hamming distance.

    Integrates with Rust hledac_rust_extensions for performance.
    Falls back gracefully if Rust extension unavailable.

    ## Capacity
    - O(n) per check_and_add() where n = stored fingerprints
    - Optimized for < 50k documents per instance
    - For larger scale: partition by domain or time bucket

    ## Example
    ```python
    from hledac.simhash_integration import NearDuplicateDetector

    detector = NearDuplicateDetector(threshold=3)

    # Check if content is near-duplicate
    is_new, dup_id = detector.check_and_add(
        "Article content here...",
        "finding-123"
    )

    if not is_new:
        print(f"Duplicate of {dup_id}")
    ```
    """

    def __init__(self, threshold: int = 3, ngram_size: int = 2):
        """
        Initialize detector.

        Args:
            threshold: Max Hamming distance for near-duplicate (3 = ~95% accuracy)
            ngram_size: Tokenization granularity (1=words, 2+=char n-grams)
        """
        self._threshold = threshold
        self._ngram_size = ngram_size
        self._store: object | None = None

        if _SIMHASH_AVAILABLE and _rust_ext is not None:
            store_class = getattr(_rust_ext, "SimHashStore", None)
            if store_class is not None:
                self._store = store_class(threshold=threshold, ngram_size=ngram_size)
        else:
            logger.warning("NearDuplicateDetector: Rust SimHash unavailable")

    def check_and_add(self, text: str, doc_id: str) -> tuple[bool, str | None]:
        """
        Check if text is near-duplicate and add to store.

        Args:
            text: Document content to check
            doc_id: Unique identifier for this document

        Returns:
            (is_new, duplicate_id):
            - is_new=True, duplicate_id=None: New document, added to store
            - is_new=False, duplicate_id=str: Near-duplicate found, returns existing ID
        """
        if self._store is None:
            logger.debug("SimHash unavailable, skipping duplicate check")
            return (True, None)

        try:
            add_doc = getattr(self._store, "add_document", None)
            if add_doc is None:
                return (True, None)
            return add_doc(text, doc_id)
        except Exception as e:
            logger.warning(f"SimHash check_and_add failed: {e}")
            return (True, None)

    def fingerprint(self, text: str) -> int:
        """Get SimHash fingerprint for text without adding to store."""
        if self._store is None:
            return 0

        try:
            fp_for = getattr(self._store, "fingerprint_for", None)
            if fp_for is None:
                return 0
            return fp_for(text)
        except Exception as e:
            logger.warning(f"SimHash fingerprint failed: {e}")
            return 0

    def __len__(self) -> int:
        """Number of documents in store."""
        if self._store is None:
            return 0
        try:
            return len(self._store)  # type: ignore[arg-type]
        except Exception:
            return 0

    def is_near_duplicate(self, text_a: str, text_b: str) -> bool:
        """
        Check if two texts are near-duplicates.

        Args:
            text_a: First text
            text_b: Second text

        Returns:
            True if Hamming distance <= threshold
        """
        if not _SIMHASH_AVAILABLE or _rust_ext is None:
            return False

        try:
            func = getattr(_rust_ext, "is_near_duplicate", None)
            if func is None:
                return False
            return func(text_a, text_b, self._threshold, self._ngram_size)
        except Exception as e:
            logger.warning(f"is_near_duplicate failed: {e}")
            return False

    def hamming_distance(self, fp_a: int, fp_b: int) -> int:
        """Compute Hamming distance between two fingerprints."""
        if not _SIMHASH_AVAILABLE or _rust_ext is None:
            return -1

        try:
            func = getattr(_rust_ext, "hamming_dist", None)
            if func is None:
                return -1
            return func(fp_a, fp_b)
        except Exception as e:
            logger.warning(f"hamming_dist failed: {e}")
            return -1

    # ---- Persistence ----

    def save_state(self, path: str | Path) -> bool:
        """
        Save detector state to file via pickle.

        Args:
            path: File path for state persistence

        Returns:
            True on success
        """
        if self._store is None:
            return False

        try:
            get_state = getattr(self._store, "__getstate__", None)
            if get_state is None:
                return False
            state = get_state()
            with open(path, "wb") as f:
                pickle.dump(state, f)
            logger.info(f"SimHash state saved: {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save SimHash state: {e}")
            return False

    @classmethod
    def load_state(cls, path: str | Path, threshold: int = 3, ngram_size: int = 2) -> "NearDuplicateDetector":
        """
        Load detector state from file.

        Args:
            path: File path with saved state
            threshold: Threshold for new detector (from saved state)
            ngram_size: N-gram size (from saved state)

        Returns:
            NearDuplicateDetector with restored state
        """
        detector = cls(threshold=threshold, ngram_size=ngram_size)

        try:
            with open(path, "rb") as f:
                state = pickle.load(f)
            set_state = getattr(detector._store, "__setstate__", None)
            if set_state is not None:
                set_state(state)
            logger.info(f"SimHash state loaded: {path}")
        except Exception as e:
            logger.error(f"Failed to load SimHash state: {e}")

        return detector


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def compute_simhash(text: str, ngram_size: int = 2) -> int:
    """
    Compute SimHash fingerprint for text.

    Args:
        text: Input text
        ngram_size: Tokenization granularity

    Returns:
        64-bit fingerprint
    """
    if not _SIMHASH_AVAILABLE or _rust_ext is None:
        raise ImportError("hledac_rust_extensions required for SimHash")
    func = getattr(_rust_ext, "simhash", None)
    if func is None:
        raise ImportError("hledac_rust_extensions.simhash not available")
    return func(text, ngram_size)


def batch_simhash(texts: list[str], ngram_size: int = 2) -> list[int]:
    """
    Compute SimHash fingerprints for batch of texts.

    Args:
        texts: List of input texts
        ngram_size: Tokenization granularity

    Returns:
        List of 64-bit fingerprints
    """
    if not _SIMHASH_AVAILABLE or _rust_ext is None:
        raise ImportError("hledac_rust_extensions required for SimHash")
    func = getattr(_rust_ext, "batch_compute_simhash", None)
    if func is None:
        raise ImportError("hledac_rust_extensions.batch_compute_simhash not available")
    return func(texts, ngram_size)
