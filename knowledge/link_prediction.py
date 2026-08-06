"""
SWARM-003: Link Prediction Module for IOC Graph

Computes link prediction scores between IOC nodes using:




- Adamic-Adar Index: Σ 1/log(degree(z)) for common neighbors
- Preferential Attachment: degree(u) × degree(v)
- Jaccard Coefficient: |N(u) ∩ N(v)| / |N(u) ∪ N(v)|

Integration with Kuzu graph (ioc_graph.py) for PREDICTED edge storage.
Auto-triggers EntropyFetchBridge micro-sprint for edge verification.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Protocol

from msgspec import Struct

if TYPE_CHECKING:
    from hledac.universal.knowledge.ioc_graph import IOCGraph

logger = logging.getLogger(__name__)


class PredictionMethod(Enum):
    """Link prediction method used."""
    ADAMIC_ADAR = auto()
    PREFERENTIAL_ATTACHMENT = auto()
    JACCARD = auto()
    COMBINED = auto()


class EdgeType(Enum):
    """IOC edge types in the graph."""
    OBSERVED = "OBSERVED"  # Directly observed edge
    PREDICTED = "PREDICTED"  # Predicted edge (needs verification)
    VERIFIED = "VERIFIED"  # Predicted edge that was verified


@dataclass(frozen=True, slots=True)
class PredictedEdge:
    """A predicted edge between two IOC nodes with confidence scores."""
    src_id: int
    dst_id: int
    adamic_adar: float
    preferential_attachment: float
    jaccard: float
    common_neighbors: int
    method: PredictionMethod
    confidence: float = field(default=0.0)

    def __post_init__(self) -> None:
        # Normalize method to enum
        if isinstance(self.method, str):
            method_map = {
                "adamic_adar": PredictionMethod.ADAMIC_ADAR,
                "pref_attach": PredictionMethod.PREFERENTIAL_ATTACHMENT,
                "jaccard": PredictionMethod.JACCARD,
                "combined": PredictionMethod.COMBINED,
            }
            object.__setattr__(self, 'method', method_map.get(self.method, PredictionMethod.COMBINED))

        # Compute confidence as weighted average
        if self.adamic_adar > 0:
            # Normalize Adamic-Adar to 0-1 range (log scale for degrees 2-1000)
            # AA = Σ 1/log(deg) → higher = more confident
            # Typical range: 0.01 to 5.0
            aa_conf = min(self.adamic_adar / 2.0, 1.0) * 0.5  # 50% weight
            jaccard_conf = self.jaccard * 0.3  # 30% weight
            pa_conf = min(self.preferential_attachment / 1000.0, 1.0) * 0.2  # 20% weight
            confidence = aa_conf + jaccard_conf + pa_conf
        else:
            confidence = 0.0

        object.__setattr__(self, 'confidence', confidence)

    @property
    def method_name(self) -> str:
        """Return method name for serialization."""
        return self.method.name.lower()

    def to_kuzu_properties(self) -> dict[str, Any]:
        """Convert to Kuzu edge properties."""
        return {
            "rel_type": EdgeType.PREDICTED.value,
            "confidence": self.confidence,
            "adamic_adar": self.adamic_adar,
            "jaccard": self.jaccard,
            "pref_attach": self.preferential_attachment,
            "common_neighbors": self.common_neighbors,
            "method": self.method_name,
        }


@dataclass(frozen=True, slots=True)
class LinkPredictionResult:
    """Result of link prediction computation."""
    edges: tuple[PredictedEdge, ...]
    total_candidates: int
    above_threshold: int
    compute_time_ms: float

    @property
    def verification_candidates(self) -> tuple[PredictedEdge, ...]:
        """Edges that should be sent to EntropyFetchBridge for verification."""
        return tuple(e for e in self.edges if e.confidence >= 0.3)

    @property
    def high_confidence(self) -> tuple[PredictedEdge, ...]:
        """High confidence edges (>= 0.7)."""
        return tuple(e for e in self.edges if e.confidence >= 0.7)


@dataclass(frozen=True, slots=True)
class LinkPredictorConfig(Struct, frozen=True, gc=False):
    """Configuration for link prediction computation."""
    min_adamic_adar: float = 0.01
    min_jaccard: float = 0.1
    max_candidates: int = 10_000
    cross_type_only: bool = False
    ioc_type_filter: tuple[str, ...] = ()
    threshold_for_verification: float = 0.3


class LinkPredictor:
    """
    Link predictor using Rust implementation over DuckDB.

    SWARM-003: Computes predicted edges between IOC nodes using:
    - Adamic-Adar Index
    - Preferential Attachment
    - Jaccard Coefficient

    M1 8GB safe: Bounded to max_candidates=10_000, runs during TEARDOWN phase.
    """

    __slots__ = (
        '_db_path',
        '_config',
        '_rust_module',
        '_available',
    )

    def __init__(
        self,
        db_path: str,
        config: LinkPredictorConfig | None = None,
    ) -> None:
        self._db_path = db_path
        self._config = config or LinkPredictorConfig()
        self._rust_module: Any | None = None
        self._available = False

        # Try to import Rust module
        self._init_rust_module()

    def _init_rust_module(self) -> None:
        """Initialize Rust link predictor module."""
        try:
            from hledac_rust_extensions import link_predictor
            self._rust_module = link_predictor
            self._available = True
            logger.debug("[SWARM-003] Link predictor Rust module loaded")
        except ImportError as e:
            logger.warning(
                "[SWARM-003] Rust link predictor not available: %s. "
                "Falling back to Python implementation.", e
            )
            self._available = False

    @property
    def is_available(self) -> bool:
        """Check if Rust implementation is available."""
        return self._available

    def predict_edges(
        self,
        config: LinkPredictorConfig | None = None,
    ) -> LinkPredictionResult:
        """
        Compute link prediction scores for all non-connected node pairs.

        Args:
            config: Optional override for default configuration

        Returns:
            LinkPredictionResult with predicted edges
        """
        cfg = config or self._config

        if self._available:
            return self._predict_edges_rust(cfg)
        else:
            return self._predict_edges_python(cfg)

    def _predict_edges_rust(
        self,
        config: LinkPredictorConfig,
    ) -> LinkPredictionResult:
        """Rust implementation of link prediction."""
        from hledac_rust_extensions.link_predictor import (
            LinkPredictorConfig as RustConfig,
            predict_links,
        )

        rust_config = RustConfig(
            min_adamic_adar=config.min_adamic_adar,
            min_jaccard=config.min_jaccard,
            max_candidates=config.max_candidates,
            cross_type_only=config.cross_type_only,
            ioc_type_filter=list(config.ioc_type_filter),
        )

        result = predict_links(self._db_path, rust_config)

        edges = tuple(
            PredictedEdge(
                src_id=e.src_id,
                dst_id=e.dst_id,
                adamic_adar=e.adamic_adar,
                preferential_attachment=e.preferential_attachment,
                jaccard=e.jaccard,
                common_neighbors=e.common_neighbors,
                method=e.method,
            )
            for e in result.edges
        )

        return LinkPredictionResult(
            edges=edges,
            total_candidates=result.total_candidates,
            above_threshold=result.above_threshold,
            compute_time_ms=result.compute_time_ms,
        )

    def _predict_edges_python(
        self,
        config: LinkPredictorConfig,
    ) -> LinkPredictionResult:
        """Python fallback implementation of link prediction."""
        import duckdb
        import math
        from collections import defaultdict

        logger.info("[SWARM-003] Using Python DuckDB fallback for link prediction")

        conn = duckdb.connect(self._db_path, read_only=True)

        try:
            # Build adjacency list
            adjacency: dict[int, list[int]] = defaultdict(list)
            degrees: dict[int, int] = defaultdict(int)

            query = """
                SELECT e.src_id, e.dst_id
                FROM ioc_edges e
                WHERE e.rel_type = 'OBSERVED'
            """
            rows = conn.execute(query).fetchall()

            for src_id, dst_id in rows:
                adjacency[src_id].append(dst_id)
                adjacency[dst_id].append(src_id)
                degrees[src_id] += 1
                degrees[dst_id] += 1

            # Deduplicate
            for node in adjacency:
                adjacency[node] = list(set(adjacency[node]))

            # Find candidates and compute scores
            candidates: dict[tuple[int, int], list[int]] = defaultdict(list)

            for node, neighbors in adjacency.items():
                for neighbor in neighbors:
                    if neighbor not in adjacency:
                        continue
                    for second in adjacency[neighbor]:
                        if second == node or second in neighbors:
                            continue
                        pair = (min(node, second), max(node, second))
                        candidates[pair].append(neighbor)

            # Limit candidates
            candidate_list = list(candidates.items())[:config.max_candidates]

            # Compute scores
            edges: list[PredictedEdge] = []

            for (src, dst), common in candidate_list:
                if not common:
                    continue

                # Adamic-Adar
                adamic_adar = 0.0
                for cn in common:
                    deg = degrees.get(cn, 0)
                    if deg > 1:
                        adamic_adar += 1.0 / math.log(deg)

                # Jaccard
                n_src = degrees.get(src, 0)
                n_dst = degrees.get(dst, 0)
                union = n_src + n_dst - len(common)
                jaccard = len(common) / union if union > 0 else 0.0

                # Preferential Attachment
                pref_attach = n_src * n_dst

                if adamic_adar >= config.min_adamic_adar and jaccard >= config.min_jaccard:
                    edges.append(PredictedEdge(
                        src_id=src,
                        dst_id=dst,
                        adamic_adar=adamic_adar,
                        preferential_attachment=float(pref_attach),
                        jaccard=jaccard,
                        common_neighbors=len(common),
                        method="adamic_adar" if adamic_adar > 0.3 else "jaccard",
                    ))

            # Sort by Adamic-Adar
            edges.sort(key=lambda e: e.adamic_adar, reverse=True)

            return LinkPredictionResult(
                edges=tuple(edges),
                total_candidates=len(candidate_list),
                above_threshold=len(edges),
                compute_time_ms=0.0,  # Not tracked in Python fallback
            )

        finally:
            conn.close()

    def predict_edges_for_node(
        self,
        node_id: int,
        top_k: int = 10,
        config: LinkPredictorConfig | None = None,
    ) -> tuple[PredictedEdge, ...]:
        """
        Get top-K predicted edges for a specific node.

        Args:
            node_id: Source node ID
            top_k: Number of predictions to return
            config: Optional configuration override

        Returns:
            Tuple of PredictedEdge sorted by Adamic-Adar score
        """
        cfg = config or self._config

        if self._available:
            from hledac_rust_extensions.link_predictor import (
                LinkPredictorConfig as RustConfig,
                predict_links_for_node,
            )

            rust_config = RustConfig(
                min_adamic_adar=cfg.min_adamic_adar,
                min_jaccard=cfg.min_jaccard,
                max_candidates=top_k,
                cross_type_only=cfg.cross_type_only,
                ioc_type_filter=list(cfg.ioc_type_filter),
            )

            result = predict_links_for_node(self._db_path, node_id, top_k, rust_config)

            return tuple(
                PredictedEdge(
                    src_id=e.src_id,
                    dst_id=e.dst_id,
                    adamic_adar=e.adamic_adar,
                    preferential_attachment=e.preferential_attachment,
                    jaccard=e.jaccard,
                    common_neighbors=e.common_neighbors,
                    method=e.method,
                )
                for e in result
            )
        else:
            # Python fallback - use general predict and filter
            result = self._predict_edges_python(cfg)
            node_edges = tuple(
                e for e in result.edges
                if e.src_id == node_id or e.dst_id == node_id
            )
            return node_edges[:top_k]

    def add_predicted_edges_to_graph(
        self,
        graph: IOCGraph,
        result: LinkPredictionResult,
        min_confidence: float = 0.3,
    ) -> int:
        """
        Add predicted edges to Kuzu graph.

        Args:
            graph: IOCGraph instance (Kuzu)
            result: Link prediction result
            min_confidence: Minimum confidence threshold for adding edge

        Returns:
            Number of edges added
        """
        count = 0
        for edge in result.edges:
            if edge.confidence >= min_confidence:
                try:
                    graph.add_predicted_edge(
                        src_id=edge.src_id,
                        dst_id=edge.dst_id,
                        confidence=edge.confidence,
                        method=edge.method_name,
                        adamic_adar=edge.adamic_adar,
                        jaccard=edge.jaccard,
                    )
                    count += 1
                except Exception as e:
                    logger.warning(
                        "[SWARM-003] Failed to add predicted edge %s -> %s: %s",
                        edge.src_id, edge.dst_id, e
                    )

        logger.info(
            "[SWARM-003] Added %d predicted edges to graph (threshold=%.2f)",
            count, min_confidence
        )
        return count


async def run_link_prediction_async(
    db_path: str,
    config: LinkPredictorConfig | None = None,
) -> LinkPredictionResult:
    """
    Async wrapper for link prediction.

    Runs link prediction in a thread pool to avoid blocking the event loop.
    """
    predictor = LinkPredictor(db_path, config)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        predictor.predict_edges,
        config,
    )


# Global link predictor instance for reuse
_link_predictor_cache: dict[str, LinkPredictor] = {}


def get_link_predictor(
    db_path: str,
    config: LinkPredictorConfig | None = None,
) -> LinkPredictor:
    """
    Get or create a cached link predictor instance.

    Args:
        db_path: Path to DuckDB database
        config: Optional configuration

    Returns:
        LinkPredictor instance
    """
    cache_key = f"{db_path}:{hash(config)}"
    if cache_key not in _link_predictor_cache:
        _link_predictor_cache[cache_key] = LinkPredictor(db_path, config)

        # Limit cache size
        if len(_link_predictor_cache) > 5:
            oldest = next(iter(_link_predictor_cache))
            del _link_predictor_cache[oldest]

    return _link_predictor_cache[cache_key]
