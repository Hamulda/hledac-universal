"""
GraphAttachmentStore — Sprint F222 extraction
=============================================

ROLE: Owns graph injection slots and graph-read seams for DuckDBShadowStore.

BOUNDARY:
    DuckDBShadowStore.__init__() creates a GraphAttachmentStore instance and
    delegates all graph-related calls to it.

GRAPH SLOTS (3 independent):
    _ioc_graph         — analytics/donor graph (DuckPGQGraph or IOCGraph)
    _stix_graph        — STIX-only synthesis graph (IOCGraph, never DuckPGQGraph)
    _truth_write_graph — ACTIVE-phase buffered write graph (IOCGraph only)

CANONICAL CONSUMERS:
    - sprint_scheduler: inject_graph() / inject_stix_graph() / inject_truth_write_graph()
    - __main__._run_sprint_mode(): get_graph_stats(), get_connected_iocs()
    - export_sprint(): get_top_seed_nodes()
    - _windup_synthesis(): get_analytics_graph_for_synthesis()
    - ghost_global upsert: get_top_entities_for_ghost_global()

STORE IS NOT GRAPH TRUTH OWNER:
    All methods are thin fail-open seams — never make DuckDBShadowStore a graph authority.
    The injected graph (DuckPGQGraph or IOCGraph) remains the authoritative backend.
"""

from __future__ import annotations

from typing import Any

__all__ = ["GraphAttachmentStore"]


def _check_graph_capability(graph: Any, method_name: str) -> None:
    """Raise TypeError if graph lacks buffer_ioc + flush_buffers (truth-write requirement)."""
    has_buffer = callable(getattr(graph, "buffer_ioc", None))
    has_flush = callable(getattr(graph, "flush_buffers", None))
    if not (has_buffer and has_flush):
        raise TypeError(
            f"{method_name}: graph must implement buffer_ioc() and flush_buffers(). "
            f"Got {graph.__class__.__name__} which lacks ACTIVE-phase buffered write capability. "
            f"Use IOCGraph (Kuzu) for truth-write slots."
        )


class GraphAttachmentStore:
    """
    Owns graph injection lifecycle and graph-read seams for DuckDBShadowStore.

    Provides 3 independent slots (each may be None independently):
      - _ioc_graph: analytics/donor graph (DuckPGQGraph or IOCGraph)
      - _stix_graph: STIX-only synthesis graph (IOCGraph)
      - _truth_write_graph: ACTIVE-phase buffered write graph (IOCGraph)

    All read seams are fail-open: errors return empty collections, not exceptions.
    """

    def __init__(self) -> None:
        # Sprint 8QA: Injectable IOCGraph instance
        # NON-AUTHORITATIVE: store is NOT graph truth owner. The injected graph
        # may be IOCGraph (Kuzu, truth) or DuckPGQGraph (donor/alternate).
        # Capability must be checked, never assumed. Set by inject_graph().
        self._ioc_graph: Any = None
        self._graph_attachment_kind: str | None = None  # class name of attached backend

        # Sprint 8VQ: Dedicated STIX-only graph slot.
        # TRUTH-STORE ONLY: only IOCGraph (Kuzu) has export_stix_bundle().
        # DuckPGQGraph is analytics/donor — never inject into _stix_graph.
        # _stix_graph is independent of _ioc_graph (analytics) and _graph_attachment_kind.
        self._stix_graph: Any = None

        # Sprint 8WA: Dedicated truth-write graph slot for ACTIVE buffered writes.
        # TRUTH-WRITE ONLY: only IOCGraph (Kuzu) supports buffer_ioc/flush_buffers.
        # This slot is INDEPENDENT of _ioc_graph (analytics/donor) and _stix_graph (STIX).
        # _truth_write_graph is used exclusively for ACTIVE-phase buffered IOC ingest.
        self._truth_write_graph: Any = None

    # -------------------------------------------------------------------------
    # IOC Graph injection (analytics/donor)
    # -------------------------------------------------------------------------

    def inject_graph(self, graph: Any) -> None:
        """
        Inject a graph instance for IOC ingest on canonical findings.

        STORE IS NOT GRAPH TRUTH OWNER — the injected graph may be:
          - IOCGraph (Kuzu): truth backend, full capability
          - DuckPGQGraph (DuckDB): donor/alternate backend, limited capability

        Capability requirements for buffered writes (ACTIVE phase):
          - Requires: buffer_ioc(), buffer_observation(), flush_buffers()
          - IOCGraph has these. DuckPGQGraph does NOT.

        After inject, use get_graph_attachment_kind() to determine
        which backend was attached and check capabilities explicitly.
        """
        self._ioc_graph = graph
        self._graph_attachment_kind = graph.__class__.__name__ if graph is not None else None

    def get_graph_attachment_kind(self) -> str | None:
        """
        NON-AUTHORITATIVE DIAGNOSTIC: returns the class name of the attached graph.

        Returns None if no graph attached.
        Use this to determine which backend is attached, then call
        hasattr/hasattr for specific capability checks before use.

        This is a COMPAT SEAM, not a canonical graph API.
        """
        return self._graph_attachment_kind

    def graph_supports_buffered_writes(self) -> bool:
        """
        NON-AUTHORITATIVE COMPAT CHECK: does attached graph support ACTIVE-phase
        buffered writes?

        Returns True only if attached graph has both:
          - buffer_ioc()
          - flush_buffers()

        IOCGraph (Kuzu): True — has full buffered write capability.
        DuckPGQGraph (DuckDB): False — has checkpoint() and add_ioc() only.

        Always check this before triggering background graph ingest,
        do not assume all injected graphs support buffered writes.
        """
        if self._ioc_graph is None:
            return False
        return (
            callable(getattr(self._ioc_graph, "buffer_ioc", None))
            and callable(getattr(self._ioc_graph, "flush_buffers", None))
        )

    # -------------------------------------------------------------------------
    # STIX Graph injection
    # -------------------------------------------------------------------------

    def inject_stix_graph(self, graph: Any) -> None:
        """
        Sprint 8VQ: Inject truth-store STIX graph for synthesis consumption.

        TRUTH-STORE ONLY: only IOCGraph (Kuzu) has export_stix_bundle().
        DuckPGQGraph must NEVER be injected here — it lacks STIX capability.

        This slot is INDEPENDENT of _ioc_graph (analytics/donor graph).
        _stix_graph is used exclusively by synthesis runners for STIX context.

        Args:
            graph: IOCGraph (Kuzu) instance or None to clear.

        Raises:
            TypeError: if graph is not None and lacks export_stix_bundle().
        """
        if graph is not None and not callable(getattr(graph, "export_stix_bundle", None)):
            raise TypeError(
                f"inject_stix_graph: graph must implement export_stix_bundle(). "
                f"Got {graph.__class__.__name__} which lacks STIX export capability. "
                f"Use IOCGraph (Kuzu) for STIX slots."
            )
        self._stix_graph = graph

    def get_stix_graph(self) -> Any:
        """
        Sprint 8VQ: Get injected STIX graph for synthesis consumers.

        Returns the injected truth-store graph (IOCGraph/Kuzu) if available,
        else None. DuckPGQGraph is never returned — it lacks export_stix_bundle().

        This is a CONSUMER-SPECIFIC seam, not a generic graph accessor.
        """
        return self._stix_graph

    # -------------------------------------------------------------------------
    # Truth-Write Graph injection (ACTIVE-phase buffered writes)
    # -------------------------------------------------------------------------

    def inject_truth_write_graph(self, graph: Any) -> None:
        """
        Sprint 8WA: Inject dedicated truth-write graph for ACTIVE buffered writes.

        TRUTH-WRITE ONLY: only IOCGraph (Kuzu) supports buffer_ioc/flush_buffers.
        DuckPGQGraph must NEVER be injected here — it lacks buffered write capability.

        This slot is INDEPENDENT of:
          - _ioc_graph (analytics/donor graph — DuckPGQGraph in windup)
          - _stix_graph (STIX synthesis graph)

        _truth_write_graph is used exclusively for ACTIVE-phase buffered IOC ingest
        via _graph_ingest_findings().

        Args:
            graph: IOCGraph (Kuzu) instance or None to clear.

        Raises:
            TypeError: if graph is not None and lacks buffer_ioc/flush_buffers.
        """
        if graph is not None:
            _check_graph_capability(graph, "inject_truth_write_graph")
        self._truth_write_graph = graph

    def get_truth_write_graph(self) -> Any:
        """
        Sprint 8WA: Get injected truth-write graph for ACTIVE-phase consumers.

        Returns the injected truth-write graph (IOCGraph/Kuzu) if available,
        else None. DuckPGQGraph is never returned — it lacks buffer_ioc/flush_buffers.

        This is a CONSUMER-SPECIFIC seam for ACTIVE-phase buffered writes only.
        """
        return self._truth_write_graph

    def truth_write_graph_supports_buffered_writes(self) -> bool:
        """
        Sprint 8WA: Does _truth_write_graph support ACTIVE-phase buffered writes?

        Returns True only if _truth_write_graph is IOCGraph (Kuzu) with both:
          - buffer_ioc()
          - flush_buffers()

        This is a dedicated check for the truth-write slot, independent of
        the analytics _ioc_graph slot.
        """
        if self._truth_write_graph is None:
            return False
        return (
            callable(getattr(self._truth_write_graph, "buffer_ioc", None))
            and callable(getattr(self._truth_write_graph, "flush_buffers", None))
        )

    # -------------------------------------------------------------------------
    # Graph-read seams (all fail-open, store is NOT graph truth owner)
    # -------------------------------------------------------------------------

    def get_top_seed_nodes(self, n: int = 5) -> list[dict]:
        """
        Sprint 8TF §1: Export-facing read-only seam for top seed nodes.

        PURPOSE
        -------
        Provides a store-facing surface for the export handoff's seed-node use case.
        export_sprint() currently falls back to store._ioc_graph.get_top_nodes_by_degree(n=5)
        directly; this method wraps that call so export consumers don't need to spelunk
        _ioc_graph internals.

        STORE IS NOT GRAPH TRUTH OWNER
        --------------------------------
        The injected graph may be IOCGraph (Kuzu, truth) or DuckPGQGraph (donor/alternate).
        This seam does NOT make DuckDBShadowStore a graph authority.
        It is a thin, fail-open adapter for one specific export-facing read-only operation.

        FUTURE OWNER / REMOVAL CONDITION
        ---------------------------------
        - Future graph truth owner: IOCGraph (Kuzu) or its successor
        - Removal condition: export_sprint() replaces its store._ioc_graph fallback
          entirely with this method, AND no other consumer accesses _ioc_graph directly
          for seed node queries

        CAPABILITY REQUIREMENTS
        -----------------------
        Requires the attached graph to implement get_top_nodes_by_degree(n).
        IOCGraph (Kuzu): has this method.
        DuckPGQGraph (DuckDB): has this method.
        If the method is absent or call fails, returns [] (fail-open).

        Args:
            n: Number of top nodes to return (default 5).

        Returns:
            list[dict]: Each dict has at least "value" and "ioc_type" keys.
            Returns [] if no graph attached or call fails.
        """
        if self._ioc_graph is None:
            return []
        try:
            method = getattr(self._ioc_graph, "get_top_nodes_by_degree", None)
            if not callable(method):
                return []
            result = method(n=n)
            # Validate return shape — expect list of dicts with value/ioc_type
            if not isinstance(result, list):
                return []
            for item in result:
                if not isinstance(item, dict) or "value" not in item:
                    return []
            return result
        except Exception:
            return []

    def get_graph_stats(self) -> dict:
        """
        Sprint 8VY: Read-only seam for analytics graph stats (DuckPGQGraph.stats()).

        PURPOSE
        -------
        Replaces direct shell access to store._ioc_graph.stats() in __main__._run_sprint_mode().
        DuckDBShadowStore is NOT a graph authority — this is a thin fail-open adapter
        for the diagnostics use case only.

        CONSUMER
        --------
        __main__._run_sprint_mode(): logging [GRAPH] nodes/edges/pgq stats.

        STORE IS NOT GRAPH TRUTH OWNER
        -------------------------------
        The analytics _ioc_graph (DuckPGQGraph) is the donor backend.
        Returns {} (fail-open) if no graph attached or call fails.

        CAPABILITY REQUIREMENTS
        -----------------------
        Requires attached graph to implement stats() → {nodes, edges, pgq_active}.
        DuckPGQGraph: has this method.
        IOCGraph: has this method.

        Returns:
            dict: {nodes, edges, pgq_active} or {} if unavailable.
        """
        if self._ioc_graph is None:
            return {}
        try:
            method = getattr(self._ioc_graph, "stats", None)
            if not callable(method):
                return {}
            result = method()
            if not isinstance(result, dict):
                return {}
            # Validate minimal shape
            if not all(k in result for k in ("nodes", "edges")):
                return {}
            return result
        except Exception:
            return {}

    def get_connected_iocs(self, ioc_value: str, max_hops: int = 2) -> list:
        """
        Sprint 8VY: Read-only seam for analytics graph find_connected() (DuckPGQGraph).

        PURPOSE
        -------
        Replaces direct shell access to store._ioc_graph.find_connected() in
        __main__._run_sprint_mode(). Diagnostic use case: log connected nodes for top IOC.
        DuckDBShadowStore is NOT a graph authority — thin fail-open adapter.

        CONSUMER
        --------
        __main__._run_sprint_mode(): logging {first_ioc} → {len(connected)} connected nodes.

        STORE IS NOT GRAPH TRUTH OWNER
        -------------------------------
        The analytics _ioc_graph (DuckPGQGraph) is the donor backend.
        Returns [] (fail-open) if no graph attached or call fails.

        CAPABILITY REQUIREMENTS
        -----------------------
        Requires attached graph to implement find_connected(value, max_hops) → list.
        DuckPGQGraph: has this method.
        IOCGraph: does NOT have this method → returns [] (fail-open).

        Args:
            ioc_value: The IOC value to find connections for.
            max_hops: Maximum traversal depth (default 2).

        Returns:
            list: Connected IOC nodes or [] if unavailable.
        """
        if self._ioc_graph is None:
            return []
        try:
            method = getattr(self._ioc_graph, "find_connected", None)
            if not callable(method):
                return []
            result = method(ioc_value, max_hops=max_hops)
            if not isinstance(result, list):
                return []
            return result
        except Exception:
            return []

    def get_connected_iocs_batch(self, values: list[str], max_hops: int = 2) -> dict[str, list]:
        """
        P1-1 fix: Batch version of get_connected_iocs for N+1 query optimization.
        Returns dict mapping each value to its connected IOC list.

        CAPABILITY REQUIREMENTS
        -----------------------
        Requires attached graph to implement find_connected_batch(values, max_hops) → dict.
        DuckPGQGraph: has this method (P1-1 fix).
        IOCGraph: does NOT have this method → returns {} (fail-open).
        """
        if not values or self._ioc_graph is None:
            return {}
        try:
            method = getattr(self._ioc_graph, "find_connected_batch", None)
            if not callable(method):
                # Fallback: individual calls
                result = {}
                for v in values:
                    result[v] = self.get_connected_iocs(v, max_hops=max_hops)
                return result
            batch_result = method(values, max_hops=max_hops)
            if not isinstance(batch_result, dict):
                return {}
            # Ensure all keys present
            return {v: batch_result.get(v, []) for v in values}
        except Exception:
            return {}

    def annotate_findings_with_graph_context(
        self,
        findings: list[dict],
        max_hops: int = 2,
        max_annotations: int = 50,
    ) -> list[dict]:
        """
        Sprint F193A §1: Read-only enrichment pass — attaches graph context to findings.

        PURPOSE
        -------
        Minimal annotation layer that reads persisted findings, queries connected IOCs
        from the graph donor backend, and attaches lightweight annotations for
        export/report use. Does NOT make DuckDBShadowStore a graph authority.

        READ-ONLY SEAM — STORE IS NOT GRAPH TRUTH OWNER
        -------------------------------------------------
        This method is a thin pass-through to graph donor backend seams:
          - get_connected_iocs() for IOC linkage
          - get_top_seed_nodes() for degree context
        It never writes to the graph. The graph (DuckPGQGraph) remains the analytics
        donor backend, not the truth owner.

        BEHAVIOR
        --------
        - Iterates through findings and extracts IOC values
        - For each unique IOC, queries get_connected_iocs() from donor graph
        - Attaches annotations as lightweight dict (no heavy objects)
        - Fail-open: returns original findings unchanged on any error
        - Bounded: max_annotations limits work to prevent unbounded work

        Args:
            findings: List of finding dicts (must have 'id' field).
            max_hops: Max traversal depth for find_connected (default 2).
            max_annotations: Max number of findings to annotate (default 50).

        Returns:
            list[dict]: Findings with optional 'graph_annotation' key attached.
            Unannotated fields are returned unchanged on failure.
        """
        if not findings or self._ioc_graph is None:
            return findings

        try:
            # Extract unique IOC values from findings
            ioc_seen: set[str] = set()
            ioc_to_finding_ids: dict[str, list[str]] = {}

            for f in findings[:max_annotations]:
                finding_id = f.get("id", "")
                # Try common IOC field names
                ioc_value = (
                    f.get("value")
                    or f.get("ioc_value")
                    or f.get("indicator")
                    or f.get("entity")
                    or ""
                )
                if ioc_value and isinstance(ioc_value, str) and len(ioc_value) >= 3:
                    if ioc_value not in ioc_seen:
                        ioc_seen.add(ioc_value)
                        ioc_to_finding_ids[ioc_value] = []
                    ioc_to_finding_ids[ioc_value].append(finding_id)

            if not ioc_seen:
                return findings

            # P1-1: N+1 query pattern — batch optimization needed
            # Current: N queries for N unique IOCs
            # P1-1 fix: Batch query — single SQL call for all IOCs instead of N calls
            connected_cache: dict[str, list[dict]] = self.get_connected_iocs_batch(
                list(ioc_seen), max_hops=max_hops
            )

            # Build annotation map
            annotation_map: dict[str, dict] = {}
            for ioc_value, connected in connected_cache.items():
                if connected:
                    annotation_map[ioc_value] = {
                        "connected_count": len(connected),
                        "connected_types": list(
                            {c.get("ioc_type", "unknown") for c in connected if c.get("ioc_type")}
                        ),
                        "max_hops": max_hops,
                        "connected_sample": connected[:5],  # lightweight sample
                    }

            # Attach annotations to findings
            enriched = []
            for f in findings[:max_annotations]:
                enriched_f = dict(f)  # shallow copy
                ioc_value = (
                    f.get("value")
                    or f.get("ioc_value")
                    or f.get("indicator")
                    or f.get("entity")
                    or ""
                )
                if ioc_value in annotation_map:
                    enriched_f["graph_annotation"] = annotation_map[ioc_value]
                enriched.append(enriched_f)

            # Append remaining findings (beyond max_annotations) unchanged
            enriched.extend(findings[max_annotations:])
            return enriched

        except Exception:
            # Fail-open: return original findings unchanged
            return findings

    def get_analytics_graph_for_synthesis(self) -> Any:
        """
        Sprint 8VY: Read-only seam replacing store._ioc_graph fallback in _windup_synthesis().

        PURPOSE
        -------
        Replaces the elif hasattr(store, "_ioc_graph") and store._ioc_graph fallback in
        _windup_synthesis(). This is the Priority 2 / analytics-donor path for synthesis.

        CONSUMER
        --------
        _windup_synthesis(): runner.inject_graph(store.get_analytics_graph_for_synthesis())

        STORE IS NOT GRAPH TRUTH OWNER
        -------------------------------
        DuckDBShadowStore is NOT graph authority. This seam explicitly labels the
        analytics donor backend. Callers must handle None.

        CAPABILITY REQUIREMENTS
        -----------------------
        DuckPGQGraph (analytics donor) has: stats, get_top_nodes_by_degree, export_edge_list.
        DuckPGQGraph does NOT have: export_stix_bundle, buffer_ioc, flush_buffers.
        For STIX, use store.get_stix_graph() (Priority 1).

        Returns:
            Any: The attached analytics graph (DuckPGQGraph) or None.
        """
        return self._ioc_graph

    def get_top_entities_for_ghost_global(
        self,
        n: int = 100,
    ) -> list[tuple[str, str, float]]:
        """
        Sprint 8TF §2: Bounded read-only seam for ghost_global cross-sprint entity accumulation.

        PURPOSE
        -------
        Provides a store-facing surface for the ghost_global upsert use case.
        __main__.py previously spelunked graph attachment internals directly:
            graph.get_nodes()[:100]  ← method does not exist on any graph backend
        This method wraps the correct capability query so __main__.py never accesses
        _ioc_graph internals for this use case.

        STORE IS NOT GRAPH TRUTH OWNER
        --------------------------------
        The injected graph is the authoritative store (IOCGraph=Kuzu or DuckPGQGraph=DuckDB).
        This seam is a thin, fail-open adapter for one specific consumer: ghost_global upsert.
        It does NOT make DuckDBShadowStore a graph authority.

        PAYLOAD SHAPE
        -------------
        Returns list[tuple[str, str, float]] — exactly the shape required by
        upsert_global_entities(entities: list[tuple[str, str, float]]).
        Each tuple: (entity_value, entity_type, confidence_cumulative)

        FUTURE OWNER / REMOVAL CONDITION
        ---------------------------------
        - Future graph truth owner: IOCGraph (Kuzu) — should expose this directly
        - Removal condition: IOCGraph.get_top_entities_for_ghost_global(n=100)
          covers this use case with no remaining __main__.py consumer

        CAPABILITY REQUIREMENTS
        ------------------------
        Requires the attached graph to implement get_top_nodes_by_degree(n).
        DuckPGQGraph (DuckDB): has this method, returns dicts with value/ioc_type/confidence.
        IOCGraph (Kuzu): does NOT have this method — returns [] (fail-open).
        Fail-open: returns [] if graph is None or method is absent.

        Args:
            n: Number of top entities to return (default 100).

        Returns:
            list[tuple[str, str, float]]: Bounded entity payload for ghost_global upsert.
            Returns [] if no graph attached or call fails.
        """
        if self._ioc_graph is None:
            return []
        try:
            method = getattr(self._ioc_graph, "get_top_nodes_by_degree", None)
            if not callable(method):
                return []
            result = method(n=n)
            if not isinstance(result, list):
                return []
            entities: list[tuple[str, str, float]] = []
            for item in result:
                if isinstance(item, dict):
                    val = item.get("value", "")
                    ioc_type = item.get("ioc_type", "unknown")
                    conf = float(item.get("confidence", 0.5))
                    if val:
                        entities.append((val, ioc_type, conf))
            return entities
        except Exception:
            return []
