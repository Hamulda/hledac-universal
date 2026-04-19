"""
Ranking Utilities - Result Fusion and Ranking Algorithms

Provides:
- Reciprocal Rank Fusion (RRF) for multi-source results
- Weighted score aggregation
- Result deduplication

References:
- Cormack, Clarke & Buettcher: "Reciprocal Rank Fusion outperforms
  Condorcet and individual Rank Learning Methods"
- Used by Google, Bing for meta-search

M1-Optimized: O(n) complexity, minimal memory footprint
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class RRFConfig:
    """Configuration for Reciprocal Rank Fusion"""
    k: int = 60  # RRF constant (60 is empirically optimal)
    max_results: int = 100
    min_score_threshold: float = 0.01
    deduplication: bool = True
    dedup_threshold: float = 0.85


@dataclass
class RankedResult:
    """Individual ranked result"""
    id: str
    title: str
    content: str
    url: Optional[str] = None
    source: str = "unknown"
    score: float = 0.0
    rank: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, RankedResult):
            return self.id == other.id
        return False


class ReciprocalRankFusion:
    """
    Reciprocal Rank Fusion for combining results from multiple sources.

    Uses formula: score = Σ 1/(k + rank) where k=60

    Example:
        >>> rrf = ReciprocalRankFusion()
        >>> scholar_results = [RankedResult(...), ...]
        >>> web_results = [RankedResult(...), ...]
        >>> fused = rrf.fuse({
        ...     "scholar": scholar_results,
        ...     "web": web_results
        ... })
    """

    def __init__(self, config: Optional[RRFConfig] = None):
        self.config = config or RRFConfig()
        self._source_stats: Dict[str, Dict[str, Any]] = defaultdict(dict)

    def _generate_id(self, result: RankedResult) -> str:
        """Generate unique ID for deduplication"""
        if result.url:
            return hashlib.md5(result.url.encode()).hexdigest()[:16]
        content_hash = hashlib.md5(
            f"{result.title}:{result.content[:200]}".encode()
        ).hexdigest()[:16]
        return content_hash

    def _normalize_text(self, text: str) -> str:
        """Normalize text for similarity comparison"""
        return ' '.join(text.lower().split())

    def _calculate_similarity(self, r1: RankedResult, r2: RankedResult) -> float:
        """Calculate simple text similarity for deduplication"""
        if r1.url and r2.url and r1.url == r2.url:
            return 1.0

        words1 = set(self._normalize_text(r1.title + " " + r1.content[:300]).split())
        words2 = set(self._normalize_text(r2.title + " " + r2.content[:300]).split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union) if union else 0.0

    def _remove_duplicates(self, results: List[RankedResult]) -> List[RankedResult]:
        """Remove near-duplicate results"""
        if not self.config.deduplication:
            return results

        unique_results: List[RankedResult] = []

        for result in results:
            is_duplicate = False
            for existing in unique_results:
                similarity = self._calculate_similarity(result, existing)
                if similarity >= self.config.dedup_threshold:
                    is_duplicate = True
                    existing.metadata.update(result.metadata)
                    if result.score > existing.score:
                        existing.score = result.score
                    break

            if not is_duplicate:
                unique_results.append(result)

        return unique_results

    def fuse(
        self,
        source_results: Dict[str, List[RankedResult]],
        source_weights: Optional[Dict[str, float]] = None
    ) -> List[RankedResult]:
        """
        Fuse results from multiple sources using RRF.

        Args:
            source_results: Dict mapping source name to list of results
            source_weights: Optional weights for each source (default: equal)

        Returns:
            Fused and ranked list of unique results
        """
        if not source_results:
            return []

        if source_weights is None:
            source_weights = {source: 1.0 for source in source_results}

        result_scores: Dict[str, tuple[RankedResult, float]] = {}

        for source, results in source_results.items():
            weight = source_weights.get(source, 1.0)

            for rank, result in enumerate(results, start=1):
                result_id = self._generate_id(result)
                result.id = result_id

                rrf_score = weight * (1.0 / (self.config.k + rank))

                if result_id in result_scores:
                    existing_result, existing_score = result_scores[result_id]
                    new_score = existing_score + rrf_score
                    result_scores[result_id] = (existing_result, new_score)
                    existing_result.metadata['sources'] = existing_result.metadata.get('sources', []) + [source]
                else:
                    result.metadata['sources'] = [source]
                    result_scores[result_id] = (result, rrf_score)

        sorted_results = sorted(
            result_scores.values(),
            key=lambda x: x[1],
            reverse=True
        )

        final_results: List[RankedResult] = []
        for rank, (result, score) in enumerate(sorted_results[:self.config.max_results], start=1):
            result.score = score
            result.rank = rank
            final_results.append(result)

        final_results = self._remove_duplicates(final_results)

        logger.info(
            f"RRF fusion complete: {len(source_results)} sources, "
            f"{sum(len(r) for r in source_results.values())} input results, "
            f"{len(final_results)} output results"
        )

        return final_results

    def get_source_statistics(self) -> Dict[str, Any]:
        """Get statistics about recent fusion operations"""
        return dict(self._source_stats)


def rrf_fuse(
    ranked_lists: list[list[tuple[str, float]]],
    k: int = 60
) -> list[str]:
    """
    Fuse multiple ranked lists using Reciprocal Rank Fusion.

    Simple functional interface for fusing ranked lists where each list
    contains (id, score) tuples.

    Formula: score(id) = sum(1 / (k + rank_i)) across all lists where id appears.

    Args:
        ranked_lists: List of ranked lists. Each list contains (id, score) tuples,
                      sorted by score descending (rank 0 = highest).
        k: RRF constant (default: 60). Higher values give more weight to lower ranks.

    Returns:
        List of IDs sorted by RRF score descending.

    Example:
        >>> list1 = [("doc_a", 0.9), ("doc_b", 0.8), ("doc_c", 0.7)]
        >>> list2 = [("doc_b", 0.95), ("doc_a", 0.7), ("doc_d", 0.6)]
        >>> result = rrf_fuse([list1, list2], k=60)
        >>> # doc_b appears in both lists with good ranks, should rank high
    """
    if not ranked_lists:
        return []

    # Accumulate RRF scores for each ID
    id_scores: Dict[str, float] = defaultdict(float)

    for ranked_list in ranked_lists:
        if not ranked_lists:
            continue
        for rank, (doc_id, _) in enumerate(ranked_list, start=1):
            rrf_score = 1.0 / (k + rank)
            id_scores[doc_id] += rrf_score

    # Sort by RRF score descending
    sorted_ids = sorted(id_scores.keys(), key=lambda x: id_scores[x], reverse=True)

    return sorted_ids


class ScoreAggregator:
    """Aggregate scores from multiple sources with configurable weights."""

    @staticmethod
    def weighted_average(
        scores: Dict[str, float],
        weights: Dict[str, float]
    ) -> float:
        """
        Calculate weighted average of scores.

        Args:
            scores: Dict of source name to score
            weights: Dict of source name to weight

        Returns:
            Weighted average score
        """
        total_score = 0.0
        total_weight = 0.0

        for source, score in scores.items():
            weight = weights.get(source, 1.0)
            total_score += score * weight
            total_weight += weight

        return total_score / total_weight if total_weight > 0 else 0.0

    @staticmethod
    def max_score(scores: Dict[str, float]) -> float:
        """Get maximum score from all sources."""
        return max(scores.values()) if scores else 0.0

    @staticmethod
    def min_score(scores: Dict[str, float]) -> float:
        """Get minimum score from all sources."""
        return min(scores.values()) if scores else 0.0


# Convenience function
def fuse_results(
    source_results: Dict[str, List[RankedResult]],
    k: int = 60,
    max_results: int = 100
) -> List[RankedResult]:
    """
    Quick fusion of results from multiple sources.

    Args:
        source_results: Dict of source name -> list of results
        k: RRF constant (default: 60)
        max_results: Maximum results to return

    Returns:
        Fused and ranked results
    """
    config = RRFConfig(k=k, max_results=max_results)
    rrf = ReciprocalRankFusion(config)
    return rrf.fuse(source_results)
