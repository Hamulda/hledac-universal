"""
graph_utils — Shared graph utilities for M1 8GB UMA.

Provides:
    lazy_ig(): Lazy igraph importer (C-core, 5-10x faster than NetworkX).

Bounded, fail-safe, M1-optimized.
"""

import logging
from typing import TYPE_CHECKING, Any
from core import aclose

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

logger = logging.getLogger(__name__)


def lazy_ig() -> Any:
    """
    Lazy import of igraph — M1-optimized C-core graph library.

    Bounded: returns None on any error (import failure, missing dep, etc.).
    Always-on: no feature flag needed.

    Why igraph over NetworkX on M1 8GB:
        - community_label_propagation: C-core, ~5-10x faster
        - degree_centrality + betweenness_centrality: C-core
        - strength(): native weighted degree via igraph C-core
        - All algorithms are O(n) or O(n·k) with small k, not O(n²)

    Usage::
        ig_mod = lazy_ig()
        if ig_mod is None:
            # fallback to pure-Python
    """
    try:
        import igraph as ig_mod  # type: ignore[import-not-found]
        return ig_mod
    except Exception as e:
        logger.debug(f"lazy_ig: igraph unavailable: {e}")
        return None


# Re-export for backwards compatibility with files that used _lazy_ig
_lazy_ig = lazy_ig
