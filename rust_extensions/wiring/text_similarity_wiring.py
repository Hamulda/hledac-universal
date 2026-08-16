"""
Text Similarity Rust Integration Wiring
======================================

Wires rust_extensions/src/text_similarity.rs to:
- recon/temporal_archaeologist.py
- intelligence/temporal_archaeologist.py

Purpose:
- Parallel trigram Jaccard similarity grouping
- O(n²) comparisons via rayon parallelism

Integration Point:
- TemporalArchaeologist._group_similar_snapshots()
- Replaces pure Python O(n²) serial comparison

Usage:
    from rust_extensions.wiring.text_similarity_wiring import group_similar_texts
    
    groups = group_similar_texts(
        [snap.content_preview for snap in snapshots],
        threshold=0.8
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Import the integration layer
from rust_extensions.integrations import get_text_similarity

# Create singleton instance
_text_similarity = get_text_similarity()


def text_similarity_wired():
    """Get the wired text similarity integration."""
    return _text_similarity


def group_similar_texts(
    texts: list[str],
    threshold: float = 0.8,
) -> list[list[int]]:
    """
    Group similar texts using trigram Jaccard similarity.

    Uses Rust rayon parallelization when available.

    Args:
        texts: List of content strings to group
        threshold: Jaccard similarity threshold [0.0, 1.0]

    Returns:
        List of groups, each group is a list of indices into original texts.
    """
    return _text_similarity.group_similar_texts(texts, threshold)


# Check availability at import time for logging
if _text_similarity.available:
    logger.info("[TextSimilarity] Rust text_similarity.rs integration: ENABLED")
else:
    logger.info("[TextSimilarity] Rust text_similarity.rs integration: DISABLED (using Python fallback)")
