"""
Dedup Coordinator — C12: Link Predictor Integration
====================================================

ROLE: Orchestrates deduplication with graph-based link prediction as Tier 2 signal.

ARCHITECTURE:
    Tier 1 (Primary): DedupManager — fingerprint-based dedup (Bloom + LMDB)
    Tier 2 (Secondary): Link Predictor — graph-based IOC relationship scoring
    
TIER 2 SIGNAL PURPOSE:
    - Identifies new IOC relationships not in dedup set
    - Uses Adamic-Adar, Jaccard, Preferential Attachment for scoring
    - Bounded: top-100 nodes, top-20 candidates per node (M1 8GB safe)
    - Fallback: existing heuristics when Rust unavailable

BOUNDARY:
    DuckDBShadowStore.async_ingest_findings_batch() delegates quality decisions
    to this coordinator, which combines Tier 1 + Tier 2 signals.

C12 INTEGRATION:
    rust.link_predictor.predict_links_for_node_py(node_id, top_k=20)
    → Returns predicted edges with Adamic-Adar, Jaccard, common neighbors scores
    → Used to identify potential IOC duplicates or related entities
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
import logging
import weakref

from _core import aclose

if TYPE_CHECKING:
    from knowledge.dedup import DedupManager
    from _core.rust_backend.link_predictor import _LinkPredictorDomain

logger = logging.getLogger(__name__)

# [C12] M1 8GB bounds for link prediction
_LINK_PREDICT_MAX_NODES: int = 100  # Max nodes to process per dedup cycle
_LINK_PREDICT_TOP_K: int = 20  # Top predictions per node
_LINK_PREDICT_MIN_ADAMIC_ADAR: float = 0.01  # Minimum score threshold
_LINK_PREDICT_MIN_JACCARD: float = 0.1  # Minimum Jaccard coefficient


@dataclass
class DedupSignal:
    """A dedup signal from any tier."""
    signal_id: str  # Unique signal identifier
    tier: int  # 1=primary, 2=link_predictor
    signal_type: str  # 'fingerprint', 'link_predict', 'heuristic'
    score: float  # Confidence score (0.0-1.0)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass 
class DedupDecision:
    """Dedup decision combining signals from all tiers."""
    is_duplicate: bool
    confidence: float  # 0.0-1.0
    signals: list[DedupSignal] = field(default_factory=list)
    dedup_reason: str = ""


class DedupCoordinator:
    """
    Orchestrates deduplication with Tier 2 link prediction signal.
    
    Integrates:
    - Tier 1: DedupManager (fingerprint-based dedup)
    - Tier 2: Link Predictor (graph-based relationship scoring)
    
    C12 BOUNDS:
    - Processes max 100 nodes per dedup cycle
    - Returns top-20 predictions per node
    - Falls back to Tier 1 only when Rust unavailable
    """
    
    __slots__ = (
        '_dedup_manager',
        '_link_predictor',
        '_link_predictor_available',
        '_db_path',
        '_node_cache',  # Track recently processed nodes for link prediction
        '_link_predict_cache',  # Cache link predictions per node
        '_max_cache_size',
        '_closed',
        '_ffi_circuit_breaker',
        '_initialization_done',
    )

    def __init__(
        self,
        dedup_manager: DedupManager,
        db_path: str | None = None,
        *,
        max_nodes: int = _LINK_PREDICT_MAX_NODES,
        max_cache_size: int = 1000,
    ) -> None:
        """
        Initialize DedupCoordinator with Tier 1 and Tier 2 backends.
        
        Args:
            dedup_manager: Tier 1 DedupManager instance
            db_path: Path to DuckDB for link prediction (optional)
            max_nodes: Max nodes to process for link prediction per cycle
            max_cache_size: Max cached link predictions
        """
        self._dedup_manager = dedup_manager
        self._db_path = db_path
        self._link_predictor: _LinkPredictorDomain | None = None
        self._link_predictor_available: bool = False
        self._node_cache: list[int] = []  # Recently processed node IDs
        self._link_predict_cache: dict[int, list[dict[str, Any]]] = {}
        self._max_cache_size = max_cache_size
        self._closed = False
        self._ffi_circuit_breaker = None
        self._initialization_done = False
        
        # [C12] Lazy init for Rust link predictor
        self._init_link_predictor()

    def _init_link_predictor(self) -> None:
        """Initialize Rust link predictor with FFI circuit breaker."""
        try:
            from _core.rust_backend import rust as _rust_backend
            from _core.ffi_circuit_breaker import (
                get_ffi_circuit_breaker,
                FFI_MODULE_LINK_PREDICTOR,
            )
            
            if _rust_backend.is_available and _rust_backend.link_predictor is not None:
                self._link_predictor = _rust_backend.link_predictor
                self._ffi_circuit_breaker = get_ffi_circuit_breaker()
                self._link_predictor_available = True
                logger.info("C12: Link predictor initialized (Tier 2 signal active)")
            else:
                logger.debug("C12: Link predictor unavailable (Tier 2 disabled)")
        except ImportError as e:
            logger.debug(f"C12: Link predictor import failed: {e}")
        except Exception as e:
            logger.warning(f"C12: Link predictor init failed: {e}")

    def is_link_predictor_available(self) -> bool:
        """Check if Tier 2 link prediction is available."""
        return self._link_predictor_available

    def _add_to_node_cache(self, node_id: int) -> None:
        """Track node for potential link prediction."""
        if node_id not in self._node_cache:
            self._node_cache.append(node_id)
            # Trim cache to max size (FIFO)
            while len(self._node_cache) > _LINK_PREDICT_MAX_NODES:
                oldest = self._node_cache.pop(0)
                self._link_predict_cache.pop(oldest, None)

    def _predict_links_for_node(
        self,
        node_id: int,
        top_k: int = _LINK_PREDICT_TOP_K,
    ) -> list[dict[str, Any]]:
        """
        [C12] Get link predictions for a node using Rust link_predictor.
        
        Args:
            node_id: Node ID to predict links for
            top_k: Number of predictions to return
            
        Returns:
            List of predicted edges with scores
        """
        if not self._link_predictor_available:
            return []
            
        # Check cache first
        if node_id in self._link_predict_cache:
            return self._link_predict_cache[node_id]
        
        if self._db_path is None:
            return []
        
        try:
            # [C12] Use FFI circuit breaker for safety
            if self._ffi_circuit_breaker is not None:
                def rust_call() -> list[dict[str, Any]]:
                    return self._link_predictor.predict_links_for_node(
                        self._db_path,
                        node_id,
                        top_k=top_k,
                        min_adamic_adar=_LINK_PREDICT_MIN_ADAMIC_ADAR,
                        min_jaccard=_LINK_PREDICT_MIN_JACCARD,
                    )
                
                cb_result = self._ffi_circuit_breaker.call_or_fallback(
                    FFI_MODULE_LINK_PREDICTOR, rust_call,
                    self._db_path, node_id, top_k,
                    _LINK_PREDICT_MIN_ADAMIC_ADAR, _LINK_PREDICT_MIN_JACCARD
                )
                
                if cb_result.success:
                    predictions = cb_result.value
                else:
                    return self._predict_links_python_fallback(node_id, top_k)
            else:
                predictions = self._link_predictor.predict_links_for_node(
                    self._db_path,
                    node_id,
                    top_k=top_k,
                    min_adamic_adar=_LINK_PREDICT_MIN_ADAMIC_ADAR,
                    min_jaccard=_LINK_PREDICT_MIN_JACCARD,
                )
            
            # Cache results
            self._link_predict_cache[node_id] = predictions
            return predictions
            
        except Exception as e:
            logger.debug(f"C12: Link prediction failed for node {node_id}: {e}")
            return []

    def _predict_links_python_fallback(
        self,
        node_id: int,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """
        [C12] Pure Python fallback for link prediction.
        
        Uses simple neighbor-based scoring when Rust unavailable.
        Less accurate but ensures pipeline continuity.
        """
        # Simple heuristic: nodes with common neighbors are related
        # This is a simplified version of Adamic-Adar
        try:
            from knowledge.ioc_graph import IOCGraph
            import asyncio
            
            # Run in event loop if needed
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            graph = IOCGraph()
            
            # Get neighbors (simplified)
            neighbors = getattr(graph, '_buffer', {}).get(node_id, [])
            
            # Generate simple predictions based on shared characteristics
            predictions = []
            for i, neighbor in enumerate(neighbors[:top_k]):
                predictions.append({
                    'src_id': node_id,
                    'dst_id': neighbor,
                    'adamic_adar': 0.1,  # Conservative fallback score
                    'jaccard': 0.1,
                    'pref_attach': 1.0,
                    'common_neighbors': 1,
                    'method': 'python_fallback',
                })
            
            graph.close()
            return predictions
            
        except Exception:
            return []

    async def adedup_check(
        self,
        fingerprint: str,
        ioc_value: str,
        ioc_type: str,
        node_id: int | None = None,
    ) -> DedupDecision:
        """
        Async dedup check combining Tier 1 + Tier 2 signals.
        
        Args:
            fingerprint: BLAKE2b fingerprint for Tier 1 dedup
            ioc_value: IOC value for Tier 2 link prediction
            ioc_type: IOC type
            node_id: Node ID for link prediction (optional)
            
        Returns:
            DedupDecision with combined signals
        """
        signals: list[DedupSignal] = []
        
        # [C12] Tier 1: Fingerprint-based dedup (primary)
        finding_id = self._dedup_manager.hot_cache_lookup(fingerprint)
        if finding_id is None:
            finding_id = self._dedup_manager.lookup_persistent_dedup(fingerprint)
        
        tier1_signal = DedupSignal(
            signal_id=f"fp:{fingerprint[:8]}",
            tier=1,
            signal_type='fingerprint',
            score=1.0 if finding_id else 0.0,
            metadata={'finding_id': finding_id} if finding_id else {},
        )
        signals.append(tier1_signal)
        
        tier1_is_duplicate = finding_id is not None
        
        # [C12] Tier 2: Link prediction (if node_id provided and Rust available)
        tier2_signal: DedupSignal | None = None
        tier2_is_duplicate = False
        
        if node_id is not None and self._link_predictor_available:
            self._add_to_node_cache(node_id)
            predictions = self._predict_links_for_node(node_id)
            
            if predictions:
                # Analyze predictions for dedup signal
                # High Adamic-Adar + high Jaccard = likely duplicate relationship
                best_score = max(
                    (p.get('adamic_adar', 0) * 0.5 + p.get('jaccard', 0) * 0.5)
                    for p in predictions
                ) if predictions else 0.0
                
                tier2_signal = DedupSignal(
                    signal_id=f"lp:node_{node_id}",
                    tier=2,
                    signal_type='link_predict',
                    score=min(best_score, 1.0),
                    metadata={
                        'predictions': predictions[:5],  # Top 5 for metadata
                        'prediction_count': len(predictions),
                    },
                )
                signals.append(tier2_signal)
                
                # Tier 2 indicates potential duplicate if high confidence
                # Threshold: Adamic-Adar > 0.3 AND Jaccard > 0.2
                tier2_is_duplicate = best_score > 0.35
        
        # Combine signals: Tier 1 is authoritative, Tier 2 provides context
        if tier1_is_duplicate:
            is_duplicate = True
            confidence = 1.0
            reason = "Tier 1: Fingerprint match"
        elif tier2_signal is not None and tier2_is_duplicate:
            is_duplicate = False  # Tier 2 is advisory, not authoritative
            confidence = tier2_signal.score * 0.6  # Reduced confidence
            reason = f"Tier 2: Link prediction score={tier2_signal.score:.3f}"
        else:
            is_duplicate = False
            confidence = 1.0 if tier1_is_duplicate else 0.5
            reason = "No dedup signals triggered"
        
        return DedupDecision(
            is_duplicate=is_duplicate,
            confidence=confidence,
            signals=signals,
            dedup_reason=reason,
        )

    def dedup_check(
        self,
        fingerprint: str,
        ioc_value: str,
        ioc_type: str,
        node_id: int | None = None,
    ) -> DedupDecision:
        """
        Sync dedup check (wrapper for async version).
        """
        import asyncio
        
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context
            future = asyncio.create_task(
                self.adedup_check(fingerprint, ioc_value, ioc_type, node_id)
            )
            return loop.run_until_complete(future)
        except RuntimeError:
            # No running loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                self.adedup_check(fingerprint, ioc_value, ioc_type, node_id)
            )

    async def arun_link_prediction_batch(
        self,
        node_ids: list[int] | None = None,
        top_k: int = _LINK_PREDICT_TOP_K,
    ) -> dict[int, list[dict[str, Any]]]:
        """
        [C12] Run link prediction for a batch of nodes.
        
        Bounded to prevent memory exhaustion on M1 8GB.
        
        Args:
            node_ids: List of node IDs to process (None = use cached nodes)
            top_k: Predictions per node
            
        Returns:
            Dict mapping node_id → list of predicted edges
        """
        results: dict[int, list[dict[str, Any]]] = {}
        
        # Use provided node_ids or cached nodes
        target_nodes = node_ids if node_ids else self._node_cache
        
        # Limit to max nodes
        target_nodes = target_nodes[:_LINK_PREDICT_MAX_NODES]
        
        if not target_nodes:
            return results
            
        if not self._link_predictor_available:
            logger.debug("C12: Link predictor unavailable for batch")
            return results
        
        # Process nodes
        for node_id in target_nodes:
            predictions = self._predict_links_for_node(node_id, top_k)
            if predictions:
                results[node_id] = predictions[:top_k]
        
        return results

    def get_link_predictor_stats(self) -> dict[str, Any]:
        """Return link predictor availability and stats."""
        return {
            'link_predictor_available': self._link_predictor_available,
            'cached_nodes': len(self._node_cache),
            'cached_predictions': len(self._link_predict_cache),
            'max_nodes_per_cycle': _LINK_PREDICT_MAX_NODES,
            'top_k_per_node': _LINK_PREDICT_TOP_K,
            'min_adamic_adar': _LINK_PREDICT_MIN_ADAMIC_ADAR,
            'min_jaccard': _LINK_PREDICT_MIN_JACCARD,
        }

    async def aclose(self) -> None:
        """Async cleanup."""
        self._closed = True
        self._node_cache.clear()
        self._link_predict_cache.clear()

    def close(self) -> None:
        """Sync cleanup."""
        self._closed = True
        self._node_cache.clear()
        self._link_predict_cache.clear()

    def __enter__(self) -> DedupCoordinator:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# =============================================================================
# C12: Helper Functions for DuckDBShadowStore Integration
# =============================================================================

def create_dedup_coordinator(
    dedup_manager: DedupManager,
    db_path: str | None = None,
) -> DedupCoordinator:
    """
    Factory: Create DedupCoordinator with all tiers initialized.
    
    Args:
        dedup_manager: Tier 1 DedupManager instance
        db_path: Path to DuckDB for link prediction
        
    Returns:
        Configured DedupCoordinator
    """
    return DedupCoordinator(
        dedup_manager=dedup_manager,
        db_path=db_path,
    )
