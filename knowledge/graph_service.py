"""
Graph Service — Sprint Memory Layer Facade
=========================================


Cross-sprint entity memory backed by DuckPGQGraph (DuckDB).

ROLE: Sprint memory / cross-sprint persistence layer.
- Idempotent upsert for entities (INSERT OR IGNORE)
- History lookup via find_connected
- Fail-safe: sprint continues on graph failure

ARCHITECTURE (F226):
- GraphService instances own only instance-isolated state: _seen_iocs, _seen_rels.
- DuckPGQGraph backend remains a module-level lazy singleton via _get_graph().
- Module-level _get_graph() is patchable for tests — both module-level functions and
  GraphService instance methods call the same module-level _get_graph().
- Module-level functions delegate to _DEFAULT_GRAPH_SERVICE (default singleton facade).
- New code should prefer injected GraphService instances for test isolation.
- Existing module-level API (reset_session) is preserved for
  backward compatibility and remains wired to the default facade instance.

MODERN-25: Sprint Data Unit Integration
=======================================
All IOC writes route through AtomicSprintPipeline for atomic semantics:
- Provenance is required on all upsert_ioc calls (no more silent loss)
- Unknown IOC types are preserved with classification_status="pending_review"
- Vector storage: DuckDB-backed DuckDBRAGStore (no LanceDB dependency)

DEPRECATED (F350M-R):
- "pending" IOC type: Preserve original type with classification_status field

ORPHANED TASK FIX (Issue #2):
    All fire-and-forget tasks (relationship callbacks) now use
    safe_create_task_tracked with TaskScope.GRAPH_SERVICE.
    This ensures cancel_all() in winddown reaches these tasks and prevents:
    - Hanging MLX kernels from untracked embeddings
    - Unreleased Mach ports from background operations
"""



import asyncio
import logging
from collections.abc import Callable
from typing import Any

from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph





    TaskScope,
    safe_create_task_tracked,
)

# Rust backend — strict import
try:
    from hledac.universal._core.rust_backend import rust

from _core import acloseexcept ImportError:
    try:
        from hledac.universal._core.rust_backend import rust
    except ImportError:
        rust = None


def _get_rust_backend():
    """Lazy getter for Rust backend."""
    return rust


def _is_ioc_dedup_available() -> bool:
    """Check if Rust IOC dedup is available at runtime."""
    r = _get_rust_backend()
    if r is None or not r.is_available:
        return False
    return r.ioc_dedup is not None


_RUST_IOC_DEDUP_AVAILABLE = _is_ioc_dedup_available()

# IocSet and RelSet are only valid if backend is available
if _RUST_IOC_DEDUP_AVAILABLE:
    IocSet: Any = _get_rust_backend().ioc_dedup.IocDedupStore  # type: ignore[assignment, misc]
else:
    IocSet: Any = None  # type: ignore[assignment, misc]
RelSet: Any = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_GRAPH_ANALYTICS_NODES: int = 500
MAX_GRAPH_ANALYTICS_TOP_K: int = 10

# ── Module-level DuckPGQGraph singleton (lazy) ─────────────────────────────────
# Used by module-level facade AND by GraphService instances via class lookup.
# Tests patching graph_service._get_graph affect all callers.

_DUCKPGQ_GRAPH: DuckPGQGraph | None = None


def _get_graph() -> DuckPGQGraph | None:
    """Lazy singleton getter for DuckPGQGraph.

    Defined at module level so tests can patch it and affect all callers
    (both module-level functions and GraphService instance methods).
    """
    global _DUCKPGQ_GRAPH
    if _DUCKPGQ_GRAPH is None:
        try:
            from hledac.universal.paths import RAMDISK_ACTIVE, RAMDISK_ROOT
            _temp_dir = str(RAMDISK_ROOT / "duckdb_tmp") if RAMDISK_ACTIVE else None
            _DUCKPGQ_GRAPH = DuckPGQGraph(temp_dir=_temp_dir)
        except Exception as e:
            logger.warning(f"[GraphService] DuckPGQGraph init failed: {e}")
            return None
    return _DUCKPGQ_GRAPH


# ── GraphService Class ─────────────────────────────────────────────────────────

class GraphService:
    """
    Instance-isolated graph service with DuckPGQGraph backing.

    Instance state:
    - _seen_iocs: idempotency set for IOCs (owned by instance)
    - _seen_rels: idempotency set for relations (owned by instance)

    The DuckPGQGraph backend is NOT stored on the instance — instance methods and
    module-level functions alike call module-level _get_graph() for the shared
    module-level singleton. This means patching graph_service._get_graph affects
    all callers uniformly, which is the intended test isolation mechanism.

    Use this class directly for test isolation or cross-sprint tenant isolation.
    """

    __slots__ = ("_seen_iocs", "_seen_rels", "_relationship_callbacks")  # _duckpgq_graph NOT stored (uses _get_graph)

    def __init__(self) -> None:
        if _RUST_IOC_DEDUP_AVAILABLE:
            self._seen_iocs: IocSet = IocSet()  # type: ignore[assignment]
            # RelSet may be None if rust_backend doesn't expose it (ioc_dedup only has IocDedupStore)
            self._seen_rels: RelSet = RelSet() if RelSet is not None else set()  # type: ignore[assignment]
        else:
            self._seen_iocs = set()  # type: ignore[assignment]
            self._seen_rels = set()  # type: ignore[assignment]
        self._relationship_callbacks: list[Callable] = []

    def register_relationship_callback(self, fn: Callable[..., None]) -> None:
        """Register callback for relationship events (src, dst, rel_type, weight)."""
        self._relationship_callbacks.append(fn)

    # ── Public API ────────────────────────────────────────────────────────────

    def upsert_ioc(
        self,
        value: str,
        ioc_type: str = "unknown",
        confidence: float = 0.5,
        source: str = "",
        observed_at: float | None = None,
        *,
        provenance: dict | None = None,
        classification_status: str = "classified",
    ) -> bool:
        """
        Idempotent IOC upsert — skip if already upserted within this sprint session.

        Idempotency is enforced via an in-memory set, so duplicate upserts within
        a sprint return False (already handled) rather than re-writing to DuckDB.

        MODERN-25: provenance is now stored with the IOC for full traceability.
        Unknown IOC types are preserved with classification_status="pending_review"
        instead of silently converting to "pending".

        Args:
            value: IOC value
            ioc_type: IOC type (e.g., "ipv4", "domain", "hash_sha256")
            confidence: Confidence score 0.0-1.0
            source: Source URL or identifier (stored in provenance)
            observed_at: Unix timestamp of observation
            provenance: Optional provenance dict with byte_offset, timestamp, source, protocol
            classification_status: "classified" or "pending_review"

        Returns:
            True if IOC was newly upserted, False if it already existed or on error.
        """
        import time as _time
        _ts = observed_at if observed_at is not None else _time.time()
        if _RUST_IOC_DEDUP_AVAILABLE:
            if self._seen_iocs.contains(value, ioc_type):
                return False
        else:
            if (value, ioc_type) in self._seen_iocs:
                return False

        # MODERN-25: Preserve unknown IOC types with classification status
        # No longer silently converting to "pending" — this caused provenance loss
        from hledac.universal.utils.ioc_extract import IOC_TYPES as _VALID_IOC_TYPES
        if ioc_type not in _VALID_IOC_TYPES:
            logger.debug(f"[GraphService] unknown ioc_type={ioc_type!r}, preserving with pending_review")
            classification_status = "pending_review"

        graph = _get_graph()
        if graph is None:
            return False
        try:
            # MODERN-25: Pass provenance dict to graph for storage
            row_id = graph.add_ioc(
                value, ioc_type, confidence, source, observed_at=_ts,
                provenance=provenance, classification_status=classification_status
            )
            if row_id is not None:
                if _RUST_IOC_DEDUP_AVAILABLE:
                    self._seen_iocs.add(value, ioc_type)
                else:
                    self._seen_iocs.add((value, ioc_type))
                return True
            return False
        except Exception as e:
            logger.warning(f"[GraphService] upsert_ioc failed for {value}: {e}")
            return False

    # ── DuckDB-backed Entity Store (MODERN-25) ────────────────────────────────
    # Vector storage: DuckDB-backed DuckDBRAGStore
    # See: knowledge.duckdb_rag_store.get_identity_store()

    def upsert_ioc_batch(
        self,
        rows: list[tuple[str, str, float, str]],
        observed_at: float | None = None,
        *,
        provenance: dict | None = None,
        classification_status: str = "classified",
    ) -> int:
        """
        Batch upsert IOCs — single DuckDB round-trip for N rows.

        Idempotency is enforced via _seen_iocs (in-memory dedup set) so duplicate
        values within a sprint are filtered before the batch is sent to DuckDB.

        MODERN-25: provenance is now stored with each IOC for full traceability.
        Unknown IOC types are preserved with classification_status="pending_review"
        instead of silently converting to "pending".

        Args:
            rows: List of (value, ioc_type, confidence, source) tuples.
                  Optional 5th element: observed_at (Unix epoch seconds).
            observed_at: Default timestamp for rows without explicit observed_at.
            provenance: Optional provenance dict with byte_offset, timestamp, source, protocol
            classification_status: "classified" or "pending_review"

        Returns:
            Number of rows passed to DuckDB (not number actually inserted).
        """
        import time as _time
        _ts = observed_at if observed_at is not None else _time.time()

        from hledac.universal.utils.ioc_extract import IOC_TYPES as _VALID_IOC_TYPES

        if not rows:
            return 0
        # [META]-012: Support 5-tuple (value, ioc_type, confidence, source, observed_at)
        # and 4-tuple (value, ioc_type, confidence, source) for backward compat
        unique: list[tuple[str, str, float, str]] = []
        unique_with_ts: list[tuple[str, str, float, str, float]] = []
        has_ts_rows = False

        for row in rows:
            if len(row) >= 5:
                value, ioc_type, confidence, source, row_ts = row[0], row[1], row[2], row[3], row[4]
                row_ts = row_ts if row_ts is not None else _ts
                has_ts_rows = True
            else:
                value, ioc_type, confidence, source = row[0], row[1], row[2], row[3]
                row_ts = _ts

            # Deduplicate via Rust IocSet or Python set
            if _RUST_IOC_DEDUP_AVAILABLE:
                if self._seen_iocs.contains(value, ioc_type):
                    continue
            else:
                key = (value, ioc_type)
                if key in self._seen_iocs:
                    continue
            # MODERN-25: Preserve unknown IOC types with classification status
            # No longer silently converting to "pending" — this caused provenance loss
            if ioc_type not in _VALID_IOC_TYPES:
                logger.debug(f"[GraphService] unknown ioc_type={ioc_type!r}, preserving with pending_review")
                classification_status = "pending_review"
            if has_ts_rows:
                unique_with_ts.append((value, ioc_type, confidence, source, row_ts))
            else:
                unique.append((value, ioc_type, confidence, source))
            if _RUST_IOC_DEDUP_AVAILABLE:
                self._seen_iocs.add(value, ioc_type)
            else:
                self._seen_iocs.add((value, ioc_type))
        if not unique and not unique_with_ts:
            return 0

        graph = _get_graph()
        if graph is None:
            return 0
        try:
            # MODERN-25: Pass provenance to batch upsert
            if has_ts_rows:
                return graph.upsert_ioc_batch(unique_with_ts, provenance=provenance, classification_status=classification_status)
            return graph.upsert_ioc_batch(unique, provenance=provenance, classification_status=classification_status)
        except Exception as e:
            logger.warning(f"[GraphService] upsert_ioc_batch failed: {e}")
            return 0

    def upsert_relation(
        self,
        src: str,
        dst: str,
        rel_type: str,
        weight: float = 1.0,
        evidence: str = ""
    ) -> bool:
        """
        Idempotent relation upsert — skip if already upserted within this sprint session.

        Returns:
            True on success, False on error or if already seen.
        """
        rel_key = (src, dst, rel_type)
        if _RUST_IOC_DEDUP_AVAILABLE and self._seen_rels.contains(src, dst, rel_type):
            return False
        if not _RUST_IOC_DEDUP_AVAILABLE and rel_key in self._seen_rels:
            return False

        graph = _get_graph()
        if graph is None:
            return False
        try:
            graph.add_relation(src, dst, rel_type, weight, evidence)
            self._seen_rels.add(rel_key)
            # Sprint P1-3 + F265-U6: hot-edges counter hook with denormalization.
            # Fetches dst's ioc_type from DuckDB (single-shot, cached RO conn)
            # and stores value+ioc_type in LMDB so read path avoids DuckDB.
            try:
                from hledac.universal.knowledge import hot_edges_cache
                src_id = hot_edges_cache.get_node_id_by_value(src)
                dst_id = hot_edges_cache.get_node_id_by_value(dst)
                if src_id is not None and dst_id is not None:
                    dst_ioc_type = ""
                    dst_value = dst
                    # F265-U6: look up dst ioc_type for denormalized storage
                    try:
                        con = hot_edges_cache._get_duckdb_ro()
                        if con is not None:
                            row = con.execute(
                                "SELECT ioc_type FROM ioc_nodes WHERE id = ?",
                                (dst_id,),
                            ).fetchone()
                            if row:
                                dst_ioc_type = str(row[0]) if row[0] else ""
                    except Exception:  # noqa: BLE001
                        pass
                    hot_edges_cache.record_edge(
                        src_id, dst_id, dst_value=dst_value, dst_ioc_type=dst_ioc_type
                    )
            except Exception:  # noqa: BLE001
                pass
            # ORPHANED TASK FIX: Fire relationship callbacks via safe_create_task_tracked
            # instead of bare running_loop.create_task(). This ensures:
            # 1. Callback tasks are registered in TaskRegistry
            # 2. cancel_all() reaches callbacks during winddown
            # 3. No orphaned relationship processing on sprint cancel
            for cb in self._relationship_callbacks:
                try:
                    result = cb(src, dst, rel_type, weight)
                    if asyncio.iscoroutine(result):
                        safe_create_task_tracked(
                            result,
                            name=f"graph:rel_callback:{src[:16]}->{dst[:16]}",
                            scope=TaskScope.GRAPH_SERVICE,
                        )
                except Exception as cb_e:
                    logger.debug("[GraphService] relationship_callback failed: %s", cb_e)
            return True
        except Exception as e:
            logger.warning(f"[GraphService] upsert_relation failed: {e}")
            return False

    def delete_relation(
        self,
        src: str,
        dst: str,
        rel_type: str,
    ) -> bool:
        """
        MODERN-25: Delete a relation from the graph.

        Used for rollback operations in AtomicSprintPipeline.

        ISSUE-FIX: Also removes from _seen_rels in-memory set so the relation
        can be re-upserted within the same session if needed.

        Args:
            src: Source IOC value
            dst: Destination IOC value
            rel_type: Relation type to delete

        Returns:
            True on success, False on error.
        """
        graph = _get_graph()
        if graph is None:
            return False
        try:
            # Use the stable node ID from quantum_pathfinder
            from hledac.universal.graph.quantum_pathfinder import _stable_node_id
            src_id = _stable_node_id(src)
            dst_id = _stable_node_id(dst)
            graph.con.execute(
                "DELETE FROM ioc_edges WHERE src_id = ? AND dst_id = ? AND rel_type = ?",
                [src_id, dst_id, rel_type],
            )
            # ISSUE-FIX: Remove from _seen_rels in-memory set so relation can be re-upserted
            rel_key = (src, dst, rel_type)
            if _RUST_IOC_DEDUP_AVAILABLE and self._seen_rels is not None:
                # Rust IocDedupStore - try to remove if it supports removal
                try:
                    if hasattr(self._seen_rels, 'remove'):
                        self._seen_rels.remove(src, dst, rel_type)
                except Exception:  # noqa: BLE001
                    pass
            elif rel_key in self._seen_rels:
                self._seen_rels.discard(rel_key)

            # Remove from hot_edges_cache if present
            try:
                from hledac.universal.knowledge import hot_edges_cache
                hot_edges_cache.delete_hot_edge(src_id, dst_id)
            except Exception:  # noqa: BLE001
                pass
            return True
        except Exception as e:
            logger.warning(f"[GraphService] delete_relation({src}→{dst}) failed: {e}")
            return False

    def upsert_identity_edge(
        self,
        src: str,
        dst: str,
        confidence: float = 0.5,
        evidence: str = "",
    ) -> bool:
        """
        F202B: Idempotent identity edge upsert — links two profile IDs as same identity.

        Convenience wrapper around upsert_relation with rel_type="same_identity".
        Advisory only: graph errors do not prevent sprint continuation.

        Returns:
            True on success, False on error or if already seen.
        """
        return self.upsert_relation(
            src=src,
            dst=dst,
            rel_type="same_identity",
            weight=confidence,
            evidence=evidence,
        )

    def find_entity_history(
        self, value: str, max_hops: int = 2,
    ) -> list[dict]:
        """
        Query entity history — find connected entities within N hops.

        Args:
            value: IOC value to query.
            max_hops: Maximum traversal depth (default 2).

        Returns:
            List of connected entity records (value, ioc_type, confidence, source),
            or empty list on error / if graph unavailable.

        Sprint P1-3: When HLEDAC_HOT_EDGES=1 and the value is present in the
        hot-edges LMDB cache, returns top-N hot neighbors first (O(1) lookup)
        and falls back to DuckPGQ recursive CTE only on cache miss. This
        avoids the O(V+E) CTE scan on dense graphs for high-degree nodes.
        """
        graph = _get_graph()
        if graph is None:
            return []
        # Sprint P1-3 + F265-U6: try hot-edges cache first
        # F265-U6: try denormalized path first (single LMDB O(1) — no DuckDB round-trip)
        try:
            from hledac.universal.knowledge import hot_edges_cache
            src_id = hot_edges_cache.get_node_id_by_value(value)
            if src_id is not None and hot_edges_cache.has_hot_edges(src_id):
                denorm_neighbors = hot_edges_cache.get_hot_neighbors_denorm(src_id, top_n=50)
                if denorm_neighbors:
                    # F265-U6: at least one entry has value+ioc_type embedded
                    has_denorm_data = any(val and ioc for _, _, val, ioc in denorm_neighbors)
                    if has_denorm_data:
                        hot_result: list[dict] = []
                        for _, _, val, ioc in denorm_neighbors:
                            if val and ioc:
                                hot_result.append({
                                    "value": val,
                                    "ioc_type": ioc,
                                    "confidence": 0.0,
                                    "source": "",
                                })
                        if hot_result:
                            return hot_result
                # Fall back to v1 format or empty denorm: use old path
                neighbors = hot_edges_cache.get_hot_neighbors(src_id, top_n=50)
                if neighbors:
                    dst_ids = [nid for nid, _cnt in neighbors]
                    records = hot_edges_cache.lookup_ioc_values_by_ids(dst_ids)
                    hot_result = []
                    for nid, _cnt in neighbors:
                        rec = records.get(nid)
                        if rec:
                            hot_result.append({
                                "value": rec.get("value", ""),
                                "ioc_type": rec.get("ioc_type", "unknown"),
                                "confidence": rec.get("confidence", 0.0),
                                "source": rec.get("source", ""),
                            })
                    if hot_result:
                        return hot_result
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # fall through to DuckPGQ
        try:
            return graph.find_connected(value, max_hops)
        except Exception as e:
            logger.warning(f"[GraphService] find_entity_history failed for {value}: {e}")
            return []

    async def find_connected_with_rerank(
        self,
        seed_value: str,
        query_embedding: list[float],
        max_hops: int = 2,
        top_k: int = 10,
        similarity_threshold: float = 0.0,
    ) -> list[dict]:
        """
        MODERN-25: Hybrid graph traversal + DuckDB vector similarity reranking.

        Uses DuckDB-backed DuckDBRAGStore from knowledge.duckdb_rag_store.

        Flow:
        1. Graph traversal via DuckPGQGraph.find_connected() — always runs.
        2. If DuckDB embeddings exist for connected IOCs, fetch + compute
           cosine similarity against query_embedding.
        3. Rerank by similarity, filter by threshold, return top_k.

        M1 8GB safe: DuckDB manages memory via hard_memory_limit.

        Args:
            seed_value: IOC value to start graph traversal from.
            query_embedding: Embedding vector for similarity reranking.
            max_hops: Graph traversal depth (default 2).
            top_k: Maximum results to return (default 10).
            similarity_threshold: Minimum cosine similarity (default 0.0 = passthrough).

        Returns:
            List of connected IOCs reranked by vector similarity, or
            plain graph results if DuckDB reranking unavailable / fails.
        """
        # Step 1: Pure graph traversal
        graph = _get_graph()
        if graph is None:
            return []
        try:
            connected = graph.find_connected(seed_value, max_hops)
        except Exception as e:
            logger.debug(f"[GraphService] graph find_connected failed: {e}")
            return []
        if not connected:
            return []

        # Step 2: DuckDB reranking (only if embedding provided)
        if query_embedding is None:
            return connected[:top_k]

        # DuckDB-backed reranking via DuckDBRAGStore
        try:
            from hledac.universal.knowledge.duckdb_rag_store import DuckDBRAGStore
            store = DuckDBRAGStore()
            connected_values = [c["value"] for c in connected]

            # DuckDB FTS5 + HNSW reranking
            reranked = await store.search_similar(
                embedding=query_embedding,
                text_hint=",".join(connected_values[:50]),
                threshold=similarity_threshold,
                limit=min(top_k * 2, len(connected_values)),
                query_type="hybrid",
            )
            if not reranked:
                return connected[:top_k]

            # Build score map
            score_map: dict[str, float] = {}
            for r in reranked:
                ioc_val = r.get("id", "")
                if ioc_val:
                    score_map[ioc_val] = float(r.get("similarity", 0.0))

            # Score and rank
            overlap = sum(1 for v in connected_values if v in score_map)
            if overlap < max(1, len(connected_values) * 0.1):
                return connected[:top_k]

            scored: list[tuple[float, dict]] = []
            for c in connected:
                val = c.get("value", "")
                sim = score_map.get(val, -1.0)
                c_copy = dict(c)
                c_copy["_similarity_score"] = sim
                scored.append((sim, c_copy))

            scored.sort(key=lambda x: x[0], reverse=True)

            result: list[dict] = []
            for sim, c in scored:
                if similarity_threshold > 0 and sim < similarity_threshold:
                    continue
                del c["_similarity_score"]
                result.append(c)
                if len(result) >= top_k:
                    break
            return result

        except ImportError:
            logger.debug("[GraphService] DuckDBRAGStore unavailable, using graph order")
            return connected[:top_k]
        except Exception as e:
            logger.debug(f"[GraphService] DuckDB rerank failed: {e}")
            return connected[:top_k]

    def find_connected_batch(self, values: list[str], max_hops: int = 2) -> dict[str, list[dict]]:
        """
        P1-1: Batch version of find_entity_history — single DuckDB round-trip.

        Args:
            values: List of IOC values to query.
            max_hops: Maximum traversal depth (default 2).

        Returns:
            Dict mapping each input value to its list of connected node dicts.
            Falls back to individual find_entity_history calls on error.
        """
        if not values:
            return {}
        graph = _get_graph()
        if graph is None:
            return {}
        try:
            # DuckPGQGraph.find_connected_batch does one CTE with IN(values) — O(1) vs N×O(V+E)
            batch_result = graph.find_connected_batch(values, max_hops)
            if batch_result:
                return batch_result
        except Exception as e:
            logger.debug(f"[GraphService] find_connected_batch failed, falling back: {e}")
        # Fallback: individual calls (fail-soft)
        result: dict[str, list[dict]] = {v: [] for v in values}
        for v in values:
            try:
                result[v] = self.find_entity_history(v, max_hops)
            except Exception:  # noqa: BLE001
                pass
        return result

    def graph_stats(self) -> dict:
        """Return graph node/edge statistics. Returns empty dict on error."""
        graph = _get_graph()
        if graph is None:
            return {}
        try:
            # F228FIX: DuckPGQGraph fallback path may not have stats() method
            if hasattr(graph, 'stats'):
                return graph.stats()
            return {}
        except Exception as e:
            logger.warning(f"[GraphService] graph_stats failed: {e}")
            return {}

    def checkpoint(self) -> None:
        """Flush WAL to disk. No-op on error."""
        graph = _get_graph()
        if graph is None:
            return
        try:
            graph.checkpoint()
        except Exception as e:
            logger.warning(f"[GraphService] checkpoint failed: {e}")

    def reset_session(self) -> None:
        """
        MODERN-35: Clear session-level idempotency trackers and graph singleton.

        Call at sprint start to prevent cross-sprint state leakage.
        Resets only this instance's state — does NOT affect other instances.

        MODERN-35 FIX: Now calls close() on the old DuckPGQGraph before
        setting _DUCKPGQ_GRAPH = None to properly release DuckDB connection
        and lock.
        """
        global _DUCKPGQ_GRAPH
        self._seen_iocs.clear()
        self._seen_rels.clear()
        # MODERN-35: Close the old graph before setting to None
        if _DUCKPGQ_GRAPH is not None:
            try:
                _DUCKPGQ_GRAPH.close()
            except Exception:  # noqa: BLE001
                pass
        _DUCKPGQ_GRAPH = None

    # ── Analytics ─────────────────────────────────────────────────────────────

    def graph_analytics_summary(
        self, top_k: int = MAX_GRAPH_ANALYTICS_TOP_K
    ) -> dict:
        """
        F206G: Bounded read-only graph analytics summary.

        Returns top_k most central entities and community count from DuckPGQGraph.
        Helper for analyst brief and sprint report — fail-soft throughout.

        Bounds:
          - MAX_GRAPH_ANALYTICS_NODES = 500  (max nodes sampled)
          - MAX_GRAPH_ANALYTICS_TOP_K = 10    (max top entities returned)

        Output keys:
          - top_central_entities: list of {value, ioc_type, degree} dicts
          - community_count: int (label-propagation estimate)
          - analytics_available: bool
          - skipped_reason: str or None

        No persistent writes. No backend re-initialization.
        """
        if top_k > MAX_GRAPH_ANALYTICS_TOP_K:
            top_k = MAX_GRAPH_ANALYTICS_TOP_K

        graph = _get_graph()
        if graph is None:
            return {
                "top_central_entities": [],
                "community_count": 0,
                "analytics_available": False,
                "skipped_reason": "graph_unavailable",
            }

        try:
            raw_top = graph.get_top_nodes_by_degree(
                n=min(top_k, MAX_GRAPH_ANALYTICS_NODES)
            )
            # F239B: confidence already in raw_top from get_top_nodes_by_degree SQL
            confidence_by_node: dict[str, float] = {}
            for row in raw_top:
                if not isinstance(row, dict):
                    continue
                v = row.get("value", "")
                c = row.get("confidence", 0.5)
                if v:
                    confidence_by_node[v] = max(0.0, min(1.0, c))

            entities = []
            for row in raw_top[:top_k]:
                if not isinstance(row, dict):
                    continue
                val = row.get("value", "")
                ioc = row.get("ioc_type", "unknown")
                deg = int(row.get("degree", 0))
                if val:
                    entities.append({
                        "value": val,
                        "ioc_type": ioc,
                        "degree": deg,
                        "max_confidence": confidence_by_node.get(val, 0.5),
                    })

            community_count = _estimate_community_count(graph)

            return {
                "top_central_entities": entities,
                "confidence_by_node": confidence_by_node,
                "community_count": community_count,
                "analytics_available": True,
                "skipped_reason": None,
            }
        except Exception as e:
            logger.warning(f"[GraphService] graph_analytics_summary failed: {e}")
            return {
                "top_central_entities": [],
                "confidence_by_node": {},
                "community_count": 0,
                "analytics_available": False,
                "skipped_reason": str(e),
            }

    def pagerank(self, max_iter: int = 100, damping: float = 0.85) -> dict[str, float]:
        """
        ISSUE #14: PageRank via DuckPGQGraph.pagerank().

        Returns:
            Dict mapping IOC value → PageRank score. Empty dict if graph unavailable.
        """
        graph = _get_graph()
        if graph is None:
            return {}
        try:
            return graph.pagerank(max_iter=max_iter, damping=damping)
        except Exception as e:
            logger.warning(f"[GraphService] pagerank failed: {e}")
            return {}

    def shortest_path(self, src: str, dst: str, max_hops: int = 10) -> list[str] | None:
        """
        ISSUE #14: Shortest path via DuckPGQGraph.shortest_path().

        Returns:
            List of IOC values forming the path, or None if no path exists.
        """
        graph = _get_graph()
        if graph is None:
            return None
        try:
            return graph.shortest_path(src=src, dst=dst, max_hops=max_hops)
        except Exception as e:
            logger.warning(f"[GraphService] shortest_path failed: {e}")
            return None

    def community_detection(self) -> dict[int, list[str]]:
        """
        ISSUE #14: Community detection via DuckPGQGraph.community_detection().

        Returns:
            Dict mapping community_id → list of IOC values in that community.
        """
        graph = _get_graph()
        if graph is None:
            return {}
        try:
            return graph.community_detection(method="louvain")
        except Exception as e:
            logger.warning(f"[GraphService] community_detection failed: {e}")
            return {}


# ── Module-level singleton facade ──────────────────────────────────────────────
# Default instance — preserves backward compatibility for code that imports
# graph_service and calls graph_service.upsert_ioc() etc.

_DEFAULT_GRAPH_SERVICE = GraphService()


# ── Module-level functions (delegate to default facade) ────────────────────────

def upsert_ioc(
    value: str,
    ioc_type: str = "unknown",
    confidence: float = 0.5,
    source: str = "",
    observed_at: float | None = None,
    *,
    provenance: dict | None = None,
    classification_status: str = "classified",
) -> bool:
    """
    MODERN-25: Pass provenance and classification_status through to GraphService.

    Args:
        provenance: Optional provenance dict with byte_offset, timestamp, source, protocol
        classification_status: "classified" or "pending_review"
    """
    return _DEFAULT_GRAPH_SERVICE.upsert_ioc(
        value, ioc_type, confidence, source, observed_at=observed_at,
        provenance=provenance, classification_status=classification_status
    )


def upsert_ioc_batch(
    rows: list[tuple[str, str, float, str]],
    observed_at: float | None = None,
    *,
    provenance: dict | None = None,
    classification_status: str = "classified",
) -> int:
    """
    MODERN-25: Pass provenance and classification_status through to GraphService.

    Args:
        provenance: Optional provenance dict with byte_offset, timestamp, source, protocol
        classification_status: "classified" or "pending_review"
    """
    return _DEFAULT_GRAPH_SERVICE.upsert_ioc_batch(
        rows, observed_at=observed_at,
        provenance=provenance, classification_status=classification_status
    )


def upsert_relation(
    src: str,
    dst: str,
    rel_type: str,
    weight: float = 1.0,
    evidence: str = ""
) -> bool:
    return _DEFAULT_GRAPH_SERVICE.upsert_relation(src, dst, rel_type, weight, evidence)


def upsert_identity_edge(
    src: str,
    dst: str,
    confidence: float = 0.5,
    evidence: str = "",
) -> bool:
    return _DEFAULT_GRAPH_SERVICE.upsert_identity_edge(src, dst, confidence, evidence)


def find_entity_history(value: str, max_hops: int = 2) -> list[dict]:
    return _DEFAULT_GRAPH_SERVICE.find_entity_history(value, max_hops)


def find_connected_batch(values: list[str], max_hops: int = 2) -> dict[str, list[dict]]:
    return _DEFAULT_GRAPH_SERVICE.find_connected_batch(values, max_hops)


def graph_stats() -> dict:
    return _DEFAULT_GRAPH_SERVICE.graph_stats()


def checkpoint() -> None:
    return _DEFAULT_GRAPH_SERVICE.checkpoint()


def reset_session() -> None:
    """
    MODERN-35: Reset session-level idempotency trackers and graph singleton.
    
    Delegates to _DEFAULT_GRAPH_SERVICE.reset_session() which:
    1. Clears instance-level _seen_iocs and _seen_rels sets
    2. Closes and nullifies the module-level _DUCKPGQ_GRAPH singleton
    
    MODERN-35 FIX: Removed duplicate close() call. GraphService.reset_session()
    already calls close() on _DUCKPGQ_GRAPH before setting it to None.
    """
    _DEFAULT_GRAPH_SERVICE.reset_session()


def shutdown_graph() -> None:
    """
    ISSUE-5.1: Shutdown the DuckPGQGraph singleton.

    Flushes buffers, checkpoints WAL, closes DuckDB connection,
    and releases the graph lock. Safe to call when graph is not initialized.

    Call this during sprint winddown AFTER DuckDBShadowStore shutdown
    to ensure proper shutdown order (DuckDB → Graph).
    """
    global _DUCKPGQ_GRAPH
    if _DUCKPGQ_GRAPH is not None:
        try:
            _DUCKPGQ_GRAPH.close()
        except Exception as e:
            logger.debug(f'[GraphService] shutdown_graph: close failed: {e}')
        _DUCKPGQ_GRAPH = None


def graph_analytics_summary(top_k: int = MAX_GRAPH_ANALYTICS_TOP_K) -> dict:
    return _DEFAULT_GRAPH_SERVICE.graph_analytics_summary(top_k)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _estimate_community_count(graph: DuckPGQGraph) -> int:
    """
    Estimate community count via DuckPGQGraph.community_detection().

    ISSUE #14: Delegates to the proper community_detection() method
    which uses iterative label propagation in DuckDB SQL.
    Returns 0 on error.
    """
    try:
        communities = graph.community_detection(method="louvain")
        if not communities:
            return 0
        return len(communities)
    except Exception:
        return 0


__all__ = [
    "GraphService",
    "upsert_ioc",
    "upsert_ioc_batch",
    "upsert_relation",
    "upsert_identity_edge",
    "find_entity_history",
    "graph_stats",
    "checkpoint",
    "reset_session",
    "shutdown_graph",
    "graph_analytics_summary",
]
