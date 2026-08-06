# link_predictor.py — Link Prediction Domain
"""
SWARM-003: Link prediction domain for IOC graph edge prediction.


Computes link prediction scores using:
- Adamic-Adar Index: Σ 1/log(degree(z)) for common neighbors
- Preferential Attachment: degree(u) × degree(v)
- Jaccard Coefficient: |N(u) ∩ N(v)| / |N(u) ∪ N(v)|

All computation runs on DuckDB ioc_nodes + ioc_edges tables.
M1 8GB safe: bounded to MAX_BATCH_NODES=10_000, runs during TEARDOWN phase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions.link_predictor import (
        LinkPredictionBatch,
        LinkPredictorConfig,
        PredictedEdgePy,
    )


class _LinkPredictorDomain:
    """
    SWARM-003: Link prediction domain with Rust-first implementation.

    Uses hledac_rust_extensions.link_predictor module for:
    - predict_links(): Full graph link prediction
    - predict_links_for_node(): Node-specific predictions
    """

    __slots__ = ('_ext',)

    def __init__(self, ext: object | None) -> None:
        self._ext = ext

    @property
    def is_available(self) -> bool:
        """Check if Rust link_predictor module is available."""
        return self._ext is not None

    def predict_links(
        self,
        db_path: str,
        min_adamic_adar: float = 0.01,
        min_jaccard: float = 0.1,
        max_candidates: int = 10000,
        cross_type_only: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Predict missing edges in the IOC graph.

        Args:
            db_path: Path to DuckDB database
            min_adamic_adar: Minimum Adamic-Adar score threshold
            min_jaccard: Minimum Jaccard coefficient threshold
            max_candidates: Maximum node pairs to consider (M1 8GB bound)
            cross_type_only: Only predict edges between different IOC types

        Returns:
            List of predicted edges with scores
        """
        if self._ext is None:
            return []

        try:
            result: LinkPredictionBatch = self._ext.predict_links(
                db_path,
                min_adamic_adar=min_adamic_adar,
                min_jaccard=min_jaccard,
                max_candidates=max_candidates,
                cross_type_only=cross_type_only,
            )
            return [
                {
                    'src_id': e.src_id,
                    'dst_id': e.dst_id,
                    'adamic_adar': e.adamic_adar,
                    'jaccard': e.jaccard,
                    'pref_attach': e.preferential_attachment,
                    'common_neighbors': e.common_neighbors,
                    'method': e.method,
                }
                for e in result.edges
            ]
        except Exception:
            return []

    def predict_links_for_node(
        self,
        db_path: str,
        node_id: int,
        top_k: int = 10,
        min_adamic_adar: float = 0.01,
        min_jaccard: float = 0.1,
    ) -> list[dict[str, Any]]:
        """
        Predict edges for a specific node.

        Args:
            db_path: Path to DuckDB database
            node_id: Source node ID
            top_k: Number of predictions to return
            min_adamic_adar: Minimum Adamic-Adar score threshold
            min_jaccard: Minimum Jaccard coefficient threshold

        Returns:
            List of predicted edges sorted by Adamic-Adar score
        """
        if self._ext is None:
            return []

        try:
            result: list[PredictedEdgePy] = self._ext.predict_links_for_node(
                db_path,
                node_id,
                top_k=top_k,
                min_adamic_adar=min_adamic_adar,
                min_jaccard=min_jaccard,
            )
            return [
                {
                    'src_id': e.src_id,
                    'dst_id': e.dst_id,
                    'adamic_adar': e.adamic_adar,
                    'jaccard': e.jaccard,
                    'pref_attach': e.preferential_attachment,
                    'common_neighbors': e.common_neighbors,
                    'method': e.method,
                }
                for e in result
            ]
        except Exception:
            return []


def get_link_predictor_domain(ext: object | None) -> _LinkPredictorDomain:
    """Factory: returns _LinkPredictorDomain wrapping Rust link_predictor module."""
    return _LinkPredictorDomain(ext)
