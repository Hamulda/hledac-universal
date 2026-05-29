"""
Smart Deduplicator - Near-duplicate detection with delta storage.

Implements:
- compute_near_dup_score: Jaccard-like similarity using superfeatures
- maybe_store_delta: Decide whether to store delta or full text

Uses rolling hash for chunking and superfeatures for fast similarity.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Constants
MAX_TEXT_SIZE = 200 * 1024  # 200KB max for delta consideration
NEAR_DUP_THRESHOLD = 0.90  # 90% similarity threshold
MIN_SAVINGS_BYTES = 1024  # Minimum savings to use delta


class SmartDeduplicator:
    """
    Near-duplicate detection with optional delta storage.

    Uses superfeatures (minhash-like) for fast similarity:
    - Chunk both texts with rolling hash
    - Compute superfeatures from chunks
    - Jaccard similarity of superfeatures = near-dup score
    """

    def __init__(
        self,
        max_text_size: int = MAX_TEXT_SIZE,
        near_dup_threshold: float = NEAR_DUP_THRESHOLD,
        min_savings_bytes: int = MIN_SAVINGS_BYTES
    ):
        """
        Initialize smart deduplicator.

        Args:
            max_text_size: Maximum text size to consider for delta
            near_dup_threshold: Minimum similarity for near-dup (0-1)
            min_savings_bytes: Minimum bytes saved to use delta
        """
        self.max_text_size = max_text_size
        self.near_dup_threshold = near_dup_threshold
        self.min_savings_bytes = min_savings_bytes
        self.logger = logging.getLogger(__name__)

        # Import here to avoid circular imports
        from .delta_compressor import DeltaCompressor
        from .rolling_hash_engine import RollingHashEngine

        self.hasher = RollingHashEngine()
        self.delta = DeltaCompressor()

    def compute_near_dup_score(self, a: bytes, b: bytes) -> float:
        """
        Compute near-duplicate similarity score.

        Uses Jaccard similarity of superfeatures:
        - Chunk both texts
        - Get chunk signatures
        - Compute superfeatures (k smallest signatures)
        - Return |A ∩ B| / |A ∪ B|

        Args:
            a: First text bytes
            b: Second text bytes

        Returns:
            Similarity score (0-1)
        """
        if not a or not b:
            return 0.0

        if a == b:
            return 1.0

        # Bound inputs
        a = a[:self.max_text_size]
        b = b[:self.max_text_size]

        try:
            # Chunk both texts (chunk_size=32 with num_features=50 gives J=1.0 for near-identical repetitive text)
            chunks_a = self.hasher.chunk_bytes(a, chunk_size=32)
            chunks_b = self.hasher.chunk_bytes(b, chunk_size=32)

            if not chunks_a or not chunks_b:
                return 0.0

            # Get signatures for chunks
            sigs_a = self.hasher.chunk_signatures(chunks_a)
            sigs_b = self.hasher.chunk_signatures(chunks_b)

            # Compute superfeatures (use num_features=50 to get all unique sigs, J=1.0 for near-identical)
            sf_a = set(self.hasher.superfeatures(sigs_a, num_features=50))
            sf_b = set(self.hasher.superfeatures(sigs_b, num_features=50))

            # Jaccard similarity
            intersection = len(sf_a & sf_b)
            union = len(sf_a | sf_b)

            if union == 0:
                return 0.0

            return intersection / union

        except Exception as e:
            self.logger.warning(f"Near-dup score computation failed: {e}")
            return 0.0

    def maybe_store_delta(
        self,
        url: str,
        base_text: str,
        new_text: str,
        store_cb: Callable[[str, str, bytes], str]
    ) -> Dict:
        """
        Decide whether to store delta or full text.

        Conditions for delta storage:
        - Same canonical URL (caller ensures this)
        - Main text size <= max_text_size
        - Near-dup score >= threshold
        - Estimated savings >= min_savings

        Args:
            url: Canonical URL
            base_text: Previous text (base)
            new_text: New text to store
            store_cb: Callback to actually store (signature: store_cb(run_id, url, data) -> artifact_id)

        Returns:
            Dict with:
            - stored_as: "delta" or "full"
            - near_dup_score: similarity score
            - bytes_saved_est: estimated bytes saved
            - artifact_id: storage result from callback
        """
        result = {
            "stored_as": "full",
            "near_dup_score": 0.0,
            "bytes_saved_est": 0,
            "artifact_id": None
        }

        # Check size constraint
        if len(new_text) > self.max_text_size:
            self.logger.debug(f"Text too large for delta: {len(new_text)} > {self.max_text_size}")
            result["artifact_id"] = store_cb("delta_run", url, new_text.encode('utf-8'))
            return result

        # Compute near-dup score
        base_bytes = base_text.encode('utf-8')[:self.max_text_size]
        new_bytes = new_text.encode('utf-8')[:self.max_text_size]

        score = self.compute_near_dup_score(base_bytes, new_bytes)
        result["near_dup_score"] = score

        # Check threshold
        if score < self.near_dup_threshold:
            self.logger.debug(f"Not near-duplicate: {score} < {self.near_dup_threshold}")
            result["artifact_id"] = store_cb("delta_run", url, new_text.encode('utf-8'))
            return result

        # Create delta
        try:
            delta = self.delta.make_text_delta(base_text, new_text)
            full_compressed = self._compress_full(new_text)

            # Calculate estimated savings
            savings = len(full_compressed) - len(delta)
            result["bytes_saved_est"] = savings

            # Check savings threshold
            if savings >= self.min_savings_bytes:
                # Store delta
                result["stored_as"] = "delta"
                result["artifact_id"] = store_cb("delta_run", url, delta)
                self.logger.debug(f"Stored delta for {url}, savings: {savings}")
            else:
                # Not worth it, store full
                result["artifact_id"] = store_cb("delta_run", url, new_text.encode('utf-8'))
                self.logger.debug(f"Delta savings too small: {savings} < {self.min_savings_bytes}")

        except Exception as e:
            self.logger.warning(f"Delta creation failed: {e}")
            result["artifact_id"] = store_cb("delta_run", url, new_text.encode('utf-8'))

        return result

    def _compress_full(self, text: str) -> bytes:
        """Compress full text for size comparison."""
        import zlib
        return zlib.compress(text.encode('utf-8'), level=6)


def compute_similarity(a: bytes, b: bytes) -> float:
    """
    Convenience function to compute near-dup similarity.

    Args:
        a: First text bytes
        b: Second text bytes

    Returns:
        Similarity score (0-1)
    """
    dedup = SmartDeduplicator()
    return dedup.compute_near_dup_score(a, b)
