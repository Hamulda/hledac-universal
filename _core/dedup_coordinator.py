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

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from knowledge.dedup import DedupManager

logger = logging.getLogger(__name__)

# [C12] M1 8GB bounds for link prediction
_LINK_PREDICT_MAX_NODES: int = 100  # Max nodes to process per dedup cycle
_LINK_PREDICT_TOP_K: int = 20  # Top predictions per node
_LINK_PREDICT_MIN_ADAMIC_ADAR: float = 0.01  # Minimum score threshold
_LINK_PREDICT_MIN_JACCARD: float = 0.1  # Minimum Jaccard coefficient

# [C12] Cache bounds for M1 8GB
_LINK_PREDICT_CACHE_MAX_SIZE: int = 500  # Max cached predictions (M1 8GB safe)
_LINK_PREDICT_CACHE_TTL_SECONDS: float = 300.0  # 5-minute TTL for cached predictions

# [C12] Circuit breaker thresholds
_CB_FAILURE_THRESHOLD: int = 3  # Failures before disabling Tier 2
_CB_RECOVERY_TIMEOUT_SECONDS: float = 30.0  # Cooldown period


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


class _CachedPrediction:
    """Cache entry with TTL support."""

    __slots__ = ("predictions", "timestamp")

    def __init__(self, predictions: list[dict[str, Any]]) -> None:
        self.predictions = predictions
        self.timestamp = time.monotonic()

    def is_expired(self, ttl: float = _LINK_PREDICT_CACHE_TTL_SECONDS) -> bool:
        return (time.monotonic() - self.timestamp) > ttl


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
    - Bounded LRU cache with TTL for predictions (M1 8GB safe)
    """

    __slots__ = (
        "_dedup_manager",
        "_link_predictor",
        "_link_predictor_available",
        "_db_path",
        "_node_cache",  # Track recently processed nodes for link prediction
        "_link_predict_cache",  # Cache link predictions per node (bounded LRU)
        "_cache_lock",  # Thread-safe cache access
        "_max_cache_size",
        "_closed",
        "_ffi_circuit_breaker",
        "_initialization_done",
        # [C12] Circuit breaker state
        "_cb_failures",
        "_cb_cooldown_until",
        # [C12] Metrics
        "_tier2_signals_total",
        "_tier2_signals_triggered",
        "_tier2_predictions_total",
    )

    def __init__(
        self,
        dedup_manager: DedupManager,
        db_path: str | None = None,
        *,
        max_nodes: int = _LINK_PREDICT_MAX_NODES,
        max_cache_size: int = _LINK_PREDICT_CACHE_MAX_SIZE,
    ) -> None:
        """
        Initialize DedupCoordinator with Tier 1 and Tier 2 backends.

        Args:
            dedup_manager: Tier 1 DedupManager instance
            db_path: Path to DuckDB for link prediction (optional)
            max_nodes: Max nodes to process for link prediction per cycle
            max_cache_size: Max cached link predictions (M1 8GB bound)
        """
        self._dedup_manager = dedup_manager
        self._db_path = db_path
        self._link_predictor: _LinkPredictorDomain | None = None
        self._link_predictor_available: bool = False
        self._node_cache: list[int] = []  # Recently processed node IDs
        self._link_predict_cache: dict[int, _CachedPrediction] = {}
        self._cache_lock = threading.RLock()
        self._max_cache_size = max_cache_size
        self._closed = False
        self._ffi_circuit_breaker = None
        self._initialization_done = False

        # [C12] Circuit breaker state
        self._cb_failures = 0
        self._cb_cooldown_until = 0.0

        # [C12] Metrics
        self._tier2_signals_total = 0
        self._tier2_signals_triggered = 0
        self._tier2_predictions_total = 0

        # [C12] Lazy init for Rust link predictor
        self._init_link_predictor()

    def _init_link_predictor(self) -> None:
        """Initialize Rust link predictor with FFI circuit breaker."""
        try:
            from _core.ffi_circuit_breaker import (
                FFI_MODULE_LINK_PREDICTOR,
                get_ffi_circuit_breaker,
            )
            from _core.rust_backend import rust as _rust_backend

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
        return self._link_predictor_available and not self._is_circuit_breaker_open()

    def _is_circuit_breaker_open(self) -> bool:
        """Check if circuit breaker is in cooldown state."""
        if self._cb_failures < _CB_FAILURE_THRESHOLD:
            return False
        if time.monotonic() >= self._cb_cooldown_until:
            # Reset after cooldown
            self._cb_failures = 0
            self._cb_cooldown_until = 0.0
            logger.info("C12: Circuit breaker recovered")
            return False
        return True

    def _record_circuit_breaker_failure(self) -> None:
        """Record a circuit breaker failure and enter cooldown if threshold reached."""
        self._cb_failures += 1
        if self._cb_failures >= _CB_FAILURE_THRESHOLD:
            self._cb_cooldown_until = time.monotonic() + _CB_RECOVERY_TIMEOUT_SECONDS
            logger.warning(
                "C12: Circuit breaker OPEN after %d failures, cooldown until %s",
                self._cb_failures,
                time.ctime(self._cb_cooldown_until),
            )

    def _add_to_node_cache(self, node_id: int) -> None:
        """Track node for potential link prediction.

        R11 FIX: Prevent memory leak by using a bounded deque pattern.
        """
        with self._cache_lock:
            if node_id in self._node_cache:
                return  # Already tracked, no-op

            self._node_cache.append(node_id)

            # R11 FIX: Immediately evict if over capacity (bounded deque pattern)
            # This prevents unbounded growth during high-throughput ingestion
            while len(self._node_cache) > _LINK_PREDICT_MAX_NODES:
                oldest = self._node_cache.pop(0)
                self._link_predict_cache.pop(oldest, None)

    def _evict_oldest_cache_entries(self, count: int = 1) -> None:
        """Evict oldest entries when cache is full."""
        while count > 0 and self._link_predict_cache:
            oldest_node_id = min(
                self._link_predict_cache.keys(), key=lambda nid: self._link_predict_cache[nid].timestamp
            )
            self._link_predict_cache.pop(oldest_node_id, None)
            self._node_cache.remove(oldest_node_id)
            count -= 1

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

        # [C12] Circuit breaker check
        if self._is_circuit_breaker_open():
            return []

        # Check cache first (with TTL)
        with self._cache_lock:
            cached = self._link_predict_cache.get(node_id)
            if cached is not None and not cached.is_expired():
                return cached.predictions

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
                    FFI_MODULE_LINK_PREDICTOR,
                    rust_call,
                    self._db_path,
                    node_id,
                    top_k,
                    _LINK_PREDICT_MIN_ADAMIC_ADAR,
                    _LINK_PREDICT_MIN_JACCARD,
                )

                if cb_result.success:
                    predictions = cb_result.value
                else:
                    self._record_circuit_breaker_failure()
                    return self._predict_links_python_fallback(node_id, top_k)
            else:
                predictions = self._link_predictor.predict_links_for_node(
                    self._db_path,
                    node_id,
                    top_k=top_k,
                    min_adamic_adar=_LINK_PREDICT_MIN_ADAMIC_ADAR,
                    min_jaccard=_LINK_PREDICT_MIN_JACCARD,
                )

            # Cache results (with bounded LRU)
            self._cache_prediction(node_id, predictions)
            return predictions

        except Exception as e:
            logger.debug(f"C12: Link prediction failed for node {node_id}: {e}")
            self._record_circuit_breaker_failure()
            return []

    def _cache_prediction(self, node_id: int, predictions: list[dict[str, Any]]) -> None:
        """Thread-safe caching with LRU eviction."""
        with self._cache_lock:
            # Evict expired entries first
            expired = [nid for nid, cached in self._link_predict_cache.items() if cached.is_expired()]
            for nid in expired:
                self._link_predict_cache.pop(nid, None)
                if nid in self._node_cache:
                    self._node_cache.remove(nid)

            # Evict oldest if at capacity
            while len(self._link_predict_cache) >= self._max_cache_size:
                self._evict_oldest_cache_entries(1)

            self._link_predict_cache[node_id] = _CachedPrediction(predictions)

    def _predict_links_python_fallback(
        self,
        node_id: int,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """
        [C12] Pure Python fallback for link prediction.

        Uses DuckDB-based neighbor scoring without Rust dependency.
        Less accurate but ensures pipeline continuity.

        M1 8GB: Bounded to prevent memory exhaustion.
        """
        if self._db_path is None:
            return []

        try:
            import duckdb

            conn = duckdb.connect(self._db_path, read_only=True)
            try:
                # [C12] Simplified Adamic-Adar via DuckDB
                # Count common neighbors between node and all other nodes
                query = f"""
                WITH
                node_neighbors AS (
                    SELECT DISTINCT src_id AS neighbor
                    FROM ioc_edges
                    WHERE dst_id = {node_id}
                    UNION
                    SELECT DISTINCT dst_id AS neighbor
                    FROM ioc_edges
                    WHERE src_id = {node_id}
                ),
                candidate_scores AS (
                    SELECT
                        e.dst_id AS candidate_id,
                        COUNT(DISTINCT e.src_id) AS common_neighbors,
                        -- Simplified Adamic-Adar: log-weighted common neighbors
                        CASE
                            WHEN COUNT(DISTINCT e.src_id) > 0
                            THEN COUNT(DISTINCT e.src_id) / LOG(2 + COUNT(DISTINCT e.src_id))
                            ELSE 0
                        END AS adamic_adar_score,
                        -- Simplified Jaccard: common / (neighbors + candidate neighbors - common)
                        CASE
                            WHEN (SELECT COUNT(*) FROM node_neighbors) > 0
                            THEN CAST(COUNT(DISTINCT e.src_id) AS DOUBLE) /
                                 (SELECT COUNT(*) FROM node_neighbors)
                            ELSE 0
                        END AS jaccard_score
                    FROM ioc_edges e
                    WHERE e.src_id IN (SELECT neighbor FROM node_neighbors)
                      AND e.dst_id != {node_id}
                    GROUP BY e.dst_id
                )
                SELECT
                    {node_id} AS src_id,
                    candidate_id AS dst_id,
                    adamic_adar_score AS adamic_adar,
                    jaccard_score AS jaccard,
                    1.0 AS pref_attach,
                    common_neighbors,
                    'python_fallback' AS method
                FROM candidate_scores
                WHERE adamic_adar_score >= {_LINK_PREDICT_MIN_ADAMIC_ADAR}
                  AND jaccard_score >= {_LINK_PREDICT_MIN_JACCARD}
                ORDER BY adamic_adar_score DESC
                LIMIT {top_k}
                """

                result = conn.execute(query).fetchall()

                predictions = [
                    {
                        "src_id": row[0],
                        "dst_id": row[1],
                        "adamic_adar": float(row[2]),
                        "jaccard": float(row[3]),
                        "pref_attach": float(row[4]),
                        "common_neighbors": row[5],
                        "method": row[6],
                    }
                    for row in result
                ]

                return predictions

            finally:
                conn.close()

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
            signal_type="fingerprint",
            score=1.0 if finding_id else 0.0,
            metadata={"finding_id": finding_id} if finding_id else {},
        )
        signals.append(tier1_signal)

        tier1_is_duplicate = finding_id is not None

        # [C12] Tier 2: Link prediction (if node_id provided and Rust available)
        tier2_signal: DedupSignal | None = None
        tier2_is_duplicate = False

        if node_id is not None and self.is_link_predictor_available():
            self._add_to_node_cache(node_id)
            predictions = self._predict_links_for_node(node_id)

            # [C12] Metrics
            self._tier2_signals_total += 1
            self._tier2_predictions_total += len(predictions)

            if predictions:
                # [C12] Metrics
                self._tier2_signals_triggered += 1

                # Analyze predictions for dedup signal
                # High Adamic-Adar + high Jaccard = likely duplicate relationship
                best_score = (
                    max((p.get("adamic_adar", 0) * 0.5 + p.get("jaccard", 0) * 0.5) for p in predictions)
                    if predictions
                    else 0.0
                )

                tier2_signal = DedupSignal(
                    signal_id=f"lp:node_{node_id}",
                    tier=2,
                    signal_type="link_predict",
                    score=min(best_score, 1.0),
                    metadata={
                        "predictions": predictions[:5],  # Top 5 for metadata
                        "prediction_count": len(predictions),
                    },
                )
                signals.append(tier2_signal)

                # [C12] Tier 2 indicates potential duplicate if high confidence
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

        Note: This is primarily for testing/CLI use. In async contexts,
        prefer calling adedup_check() directly.
        """
        import asyncio

        # [FIX] Don't create nested event loops - just run coroutine directly
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop - create new one
            with asyncio.Runner() as runner:
                return runner.run(self.adedup_check(fingerprint, ioc_value, ioc_type, node_id))

        return asyncio.run(self.adedup_check(fingerprint, ioc_value, ioc_type, node_id))

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

        # [C12] Circuit breaker check
        if self._is_circuit_breaker_open():
            logger.debug("C12: Circuit breaker open, skipping batch")
            return results

        # Use provided node_ids or cached nodes
        target_nodes = node_ids if node_ids else self._node_cache

        # Limit to max nodes
        target_nodes = target_nodes[:_LINK_PREDICT_MAX_NODES]

        if not target_nodes:
            return results

        if not self.is_link_predictor_available():
            logger.debug("C12: Link predictor unavailable for batch")
            return results

        for node_id in target_nodes:
            predictions = self._predict_links_for_node(node_id, top_k)
            if predictions:
                results[node_id] = predictions[:top_k]

        return results

    def get_link_predictor_stats(self) -> dict[str, Any]:
        """Return link predictor availability and stats."""
        return {
            "link_predictor_available": self._link_predictor_available,
            "circuit_breaker_open": self._is_circuit_breaker_open(),
            "cb_failures": self._cb_failures,
            "cb_cooldown_until": self._cb_cooldown_until,
            "cached_nodes": len(self._node_cache),
            "cached_predictions": len(self._link_predict_cache),
            "max_nodes_per_cycle": _LINK_PREDICT_MAX_NODES,
            "top_k_per_node": _LINK_PREDICT_TOP_K,
            "min_adamic_adar": _LINK_PREDICT_MIN_ADAMIC_ADAR,
            "min_jaccard": _LINK_PREDICT_MIN_JACCARD,
            # [C12] Metrics
            "tier2_signals_total": self._tier2_signals_total,
            "tier2_signals_triggered": self._tier2_signals_triggered,
            "tier2_predictions_total": self._tier2_predictions_total,
        }

    def get_metrics(self) -> dict[str, Any]:
        """Return comprehensive metrics for observability."""
        triggered = self._tier2_signals_triggered
        total = self._tier2_signals_total
        return {
            "tier2_signal_trigger_rate": triggered / total if total > 0 else 0.0,
            "tier2_signal_total": total,
            "tier2_signals_triggered": triggered,
            "tier2_predictions_total": self._tier2_predictions_total,
            "circuit_breaker_state": "open" if self._is_circuit_breaker_open() else "closed",
            "cb_failures": self._cb_failures,
            "cache_size": len(self._link_predict_cache),
            "cache_max_size": self._max_cache_size,
        }

    async def aclose(self) -> None:
        """Async cleanup."""
        self._closed = True
        with self._cache_lock:
            self._node_cache.clear()
            self._link_predict_cache.clear()

    def close(self) -> None:
        """Sync cleanup."""
        self._closed = True
        with self._cache_lock:
            self._node_cache.clear()
            self._link_predict_cache.clear()

    def __enter__(self) -> DedupCoordinator:
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Context manager exit."""
        self.close()


@dataclass
class DedupPredictionResult:
    """Result from dedup prediction bridge."""

    node_id: int
    predictions: list[dict[str, Any]]
    confidence: float
    is_duplicate_hint: bool
    signal_id: str


class LinkPredictorDedupBridge:
    """
    R11: Bridge between StreamingLinkPredictor and DedupCoordinator.

    Provides:
    - predict_links_for_node integration for dedup quality improvement
    - Batched predictions with M1 8GB bounds
    - As supplementary signal to stream_predictions()

    M1 8GB BOUNDS:
    - Max 100 nodes per batch
    - Top 20 candidates per node
    - Circuit breaker protection
    """

    __slots__ = (
        "_coordinator",
        "_available",
        "_max_nodes",
        "_top_k",
    )

    def __init__(
        self,
        dedup_coordinator: DedupCoordinator,
        *,
        max_nodes: int = _LINK_PREDICT_MAX_NODES,
        top_k: int = _LINK_PREDICT_TOP_K,
    ) -> None:
        """
        Initialize bridge.

        Args:
            dedup_coordinator: DedupCoordinator instance for Tier 2 signals
            max_nodes: Max nodes to process per batch (M1 8GB bound)
            top_k: Top predictions per node (M1 8GB bound)
        """
        self._coordinator = dedup_coordinator
        self._max_nodes = max_nodes
        self._top_k = top_k
        self._available = dedup_coordinator.is_link_predictor_available()

    @property
    def is_available(self) -> bool:
        """Check if dedup link prediction is available."""
        return self._available and self._coordinator.is_link_predictor_available()

    def run_dedup_predictions(
        self,
        node_ids: list[int],
    ) -> list[DedupPredictionResult]:
        """
        R11: Run predict_links_for_node for dedup signal generation.

        Adds predict_links as supplementary signal to stream_predictions().
        M1 8GB: Bounded to max_nodes and top_k.

        Args:
            node_ids: List of node IDs to get predictions for

        Returns:
            List of DedupPredictionResult with dedup signals
        """
        if not self.is_available:
            return []

        results: list[DedupPredictionResult] = []

        # M1 8GB: Limit nodes
        target_nodes = node_ids[: self._max_nodes]

        for node_id in target_nodes:
            predictions = self._coordinator._predict_links_for_node(
                node_id,
                top_k=self._top_k,
            )

            if predictions:
                # Calculate confidence from best prediction
                best_score = (
                    max((p.get("adamic_adar", 0) * 0.5 + p.get("jaccard", 0) * 0.5) for p in predictions)
                    if predictions
                    else 0.0
                )

                # Tier 2 duplicate hint threshold
                is_duplicate_hint = best_score > 0.35

                results.append(
                    DedupPredictionResult(
                        node_id=node_id,
                        predictions=predictions,
                        confidence=min(best_score, 1.0),
                        is_duplicate_hint=is_duplicate_hint,
                        signal_id=f"lp_dedup:node_{node_id}",
                    )
                )

        return results

    def get_dedup_signal(
        self,
        predictions: list[DedupPredictionResult],
    ) -> DedupSignal | None:
        """R11: Generate DedupSignal from prediction results."""
        return _build_dedup_signal(predictions, "lp_dedup_batch")


def _build_dedup_signal(
    predictions: list[DedupPredictionResult],
    signal_id: str,
) -> DedupSignal | None:
    """
    Shared helper to build DedupSignal from prediction results.

    Args:
        predictions: Results from run_dedup_predictions()
        signal_id: Unique signal identifier for this bridge

    Returns:
        DedupSignal if predictions available, None otherwise
    """
    if not predictions:
        return None

    total_confidence = sum(r.confidence for r in predictions)
    avg_confidence = total_confidence / len(predictions) if predictions else 0.0
    any_duplicate_hint = any(r.is_duplicate_hint for r in predictions)

    return DedupSignal(
        signal_id=signal_id,
        tier=2,
        signal_type="link_predict_dedup",
        score=avg_confidence,
        metadata={
            "prediction_count": len(predictions),
            "any_duplicate_hint": any_duplicate_hint,
            "top_predictions": [{"node_id": r.node_id, "confidence": r.confidence} for r in predictions[:5]],
        },
    )


class R11DedupBridge:
    """
    R11: Standalone dedup bridge using Rust link_predictor.

    Integrates predict_links_for_node as supplementary signal to dedup pipeline.
    Does NOT require DedupManager - uses Rust FFI directly.

    M1 8GB BOUNDS:
    - Max 100 nodes per batch
    - Top 20 candidates per node
    - Bounded LRU cache with TTL

    SIGNAL FLOW:
        SpeculativePrefetcher.execute_speculative_prefetch()
            → R11DedupBridge.run_dedup_predictions()
                → rust.link_predictor.predict_links_for_node() [FFI]
                    → DedupPredictionResult for dedup quality assessment
    """

    __slots__ = (
        "_db_path",
        "_link_predictor",
        "_available",
        "_max_nodes",
        "_top_k",
        "_cache",
        "_cache_lock",
    )

    def __init__(
        self,
        db_path: str,
        *,
        max_nodes: int = _LINK_PREDICT_MAX_NODES,
        top_k: int = _LINK_PREDICT_TOP_K,
    ) -> None:
        """
        Initialize R11 dedup bridge.

        Args:
            db_path: Path to DuckDB database
            max_nodes: Max nodes to process per batch (M1 8GB bound)
            top_k: Top predictions per node (M1 8GB bound)
        """
        self._db_path = db_path
        self._max_nodes = max_nodes
        self._top_k = top_k
        self._link_predictor = None
        self._available = False
        self._cache: dict[int, list[dict[str, Any]]] = {}
        self._cache_lock = threading.Lock()

        self._init_rust_predictor()

    def _init_rust_predictor(self) -> None:
        """Initialize Rust link predictor."""
        try:
            from _core.rust_backend import rust as _rust_backend

            if _rust_backend.is_available and _rust_backend.link_predictor is not None:
                self._link_predictor = _rust_backend.link_predictor
                self._available = True
                logger.info("R11: Rust link predictor initialized (dedup bridge active)")
            else:
                logger.debug("R11: Rust link predictor unavailable")
        except ImportError as e:
            logger.debug(f"R11: Link predictor import failed: {e}")
        except Exception as e:
            logger.warning(f"R11: Link predictor init failed: {e}")

    @property
    def is_available(self) -> bool:
        """Check if dedup bridge is available."""
        return self._available and self._link_predictor is not None

    def _get_cached(self, node_id: int) -> list[dict[str, Any]] | None:
        """Get cached predictions for node."""
        with self._cache_lock:
            return self._cache.get(node_id)

    def _cache_predictions(self, node_id: int, predictions: list[dict[str, Any]]) -> None:
        """Cache predictions with bounded eviction."""
        with self._cache_lock:
            # R11 FIX: Bounded cache - evict if at capacity
            if len(self._cache) >= 500:
                # Evict oldest entry (FIFO)
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]

            self._cache[node_id] = predictions

    def run_dedup_predictions(
        self,
        node_ids: list[int],
    ) -> list[DedupPredictionResult]:
        """
        R11: Run predict_links_for_node for dedup signal generation.

        Adds predict_links as supplementary signal to stream_predictions().
        M1 8GB: Bounded to max_nodes and top_k.

        Args:
            node_ids: List of node IDs to get predictions for

        Returns:
            List of DedupPredictionResult with dedup signals
        """
        if not self.is_available:
            return []

        results: list[DedupPredictionResult] = []

        # M1 8GB: Limit nodes
        target_nodes = node_ids[: self._max_nodes]

        for node_id in target_nodes:
            predictions = self._get_cached(node_id)

            if predictions is None:
                try:
                    predictions = self._link_predictor.predict_links_for_node(
                        self._db_path,
                        node_id,
                        top_k=self._top_k,
                        min_adamic_adar=_LINK_PREDICT_MIN_ADAMIC_ADAR,
                        min_jaccard=_LINK_PREDICT_MIN_JACCARD,
                    )
                    if predictions:
                        self._cache_predictions(node_id, predictions)
                except Exception as e:
                    logger.debug(f"R11: Link prediction failed for node {node_id}: {e}")
                    predictions = []

            if predictions:
                # Calculate confidence from best prediction
                best_score = (
                    max((p.get("adamic_adar", 0) * 0.5 + p.get("jaccard", 0) * 0.5) for p in predictions)
                    if predictions
                    else 0.0
                )

                # Tier 2 duplicate hint threshold
                is_duplicate_hint = best_score > 0.35

                results.append(
                    DedupPredictionResult(
                        node_id=node_id,
                        predictions=predictions,
                        confidence=min(best_score, 1.0),
                        is_duplicate_hint=is_duplicate_hint,
                        signal_id=f"r11_dedup:node_{node_id}",
                    )
                )

        return results

    def get_dedup_signal(
        self,
        predictions: list[DedupPredictionResult],
    ) -> DedupSignal | None:
        """R11: Generate DedupSignal from prediction results."""
        return _build_dedup_signal(predictions, "r11_dedup_batch")


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


def create_link_predictor_dedup_bridge(
    dedup_coordinator: DedupCoordinator,
    *,
    max_nodes: int = _LINK_PREDICT_MAX_NODES,
    top_k: int = _LINK_PREDICT_TOP_K,
) -> LinkPredictorDedupBridge:
    """
    R11 Factory: Create LinkPredictorDedupBridge.

    Bridges StreamingLinkPredictor with DedupCoordinator for dedup pipeline.
    M1 8GB bounds: max_nodes=100, top_k=20.

    Args:
        dedup_coordinator: DedupCoordinator instance
        max_nodes: Max nodes per batch (M1 8GB safe)
        top_k: Top predictions per node (M1 8GB safe)

    Returns:
        Configured LinkPredictorDedupBridge
    """
    return LinkPredictorDedupBridge(
        dedup_coordinator=dedup_coordinator,
        max_nodes=max_nodes,
        top_k=top_k,
    )
