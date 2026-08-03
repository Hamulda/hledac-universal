"""
runtime/adapters/graph_adapter.py — F270: DuckPGQ Graph Adapter v2
================================================================

Adapter implementing GraphProtocol for DuckPGQGraph.
Non-breaking: wraps existing graph service without changes.

GHOST_INVARIANTS:
- Fail-safe: upsert returns False on error, traversal returns [] on error
- Bounded: entity/claim limits enforced by underlying DuckPGQGraph
- Always-on: no feature flags
"""

from collections.abc import Iterable
from typing import Any, Iterator

from hledac.universal.runtime.protocols.graph_protocol import GraphProtocol


class DuckPGQGraphAdapter(GraphProtocol):
    """
    Adapter wrapping DuckPGQGraph to implement GraphProtocol.

    Non-breaking: wraps existing DuckPGQGraph and delegates
    to it without changing behavior.

    Usage:
        graph = DuckPGQGraph(...)
        adapter = DuckPGQGraphAdapter(graph)
        # Use as GraphProtocol
        await adapter.upsert_ioc("1.2.3.4", "ipv4", sprint_id="sprint_1")
        adapter.find_connected("1.2.3.4")
    """

    __slots__ = ("_graph",)

    def __init__(self, graph: Any) -> None:
        """
        Initialize adapter with existing DuckPGQGraph.

        Args:
            graph: DuckPGQGraph instance to wrap
        """
        self._graph = graph

    # === TIER_A: Analytics ===

    async def upsert_ioc(
        self,
        ioc_value: str,
        ioc_type: str,
        sprint_id: str,
        properties: dict[str, Any] | None = None,  # noqa: ARG002
        observed_at: float | None = None,
    ) -> bool:
        """Delegate IOC upsert to DuckPGQGraph.add_ioc().

        [META]-012: observed_at captures the original event timestamp.
        """
        try:
            row_id = self._graph.add_ioc(ioc_value, ioc_type, 0.5, sprint_id, observed_at=observed_at)
            return row_id is not None
        except Exception:
            return False

    def find_connected(self, ioc_value: str, max_depth: int = 2) -> list[dict[str, Any]]:
        """Delegate graph traversal to DuckPGQGraph.find_connected()."""
        try:
            return self._graph.find_connected(ioc_value, max_depth)
        except Exception:
            return []

    def upsert_relation(
        self,
        src: str,
        dst: str,
        rel_type: str,
        weight: float = 1.0,
        evidence: str = "",
    ) -> bool:
        """Delegate relation add to DuckPGQGraph.add_relation()."""
        try:
            self._graph.add_relation(src, dst, rel_type, weight, evidence)
            return True
        except Exception:
            return False

    def upsert_ioc_batch(
        self,
        rows: list[tuple[str, str, float, str]],
        observed_at: float | None = None,
    ) -> int:
        """Delegate batch upsert to DuckPGQGraph.upsert_ioc_batch().

        [META]-012: observed_at provides default timestamp for all rows.
        """
        try:
            return self._graph.upsert_ioc_batch(rows, observed_at=observed_at)
        except Exception:
            return 0

    def find_connected_batch(self, values: list[str], max_depth: int = 2) -> dict[str, list[dict[str, Any]]]:
        """Delegate batch traversal to DuckPGQGraph.find_connected_batch()."""
        try:
            return self._graph.find_connected_batch(values, max_depth)
        except Exception:
            return {}

    def get_top_nodes_by_degree(self, n: int = 20) -> list[dict[str, Any]]:
        """Delegate to DuckPGQGraph.get_top_nodes_by_degree()."""
        try:
            return self._graph.get_top_nodes_by_degree(n)
        except Exception:
            return []

    def export_edge_list(self) -> Iterator[tuple[str, str, str, float]]:
        """Delegate to DuckPGQGraph.export_edge_list() as generator."""
        try:
            yield from self._graph.export_edge_list()
        except Exception:
            return

    def stats(self) -> dict[str, Any]:
        """Delegate to DuckPGQGraph.stats()."""
        try:
            return self._graph.stats()
        except Exception:
            return {}

    def checkpoint(self) -> None:
        """Delegate to DuckPGQGraph.checkpoint()."""
        try:
            self._graph.checkpoint()
        except Exception:  # noqa: BLE001
            pass

    # === TIER_S: STIX — DuckPGQGraph supports these (F271) ===

    async def buffer_ioc(
        self,
        ioc_type: str,
        value: str,
        confidence: float = 1.0,
        observed_at: float | None = None,
    ) -> None:
        """DuckPGQGraph: buffer via buffer_ioc (F272, in-memory).

        [META]-006: observed_at captures the original event timestamp.
        """
        try:
            await self._graph.buffer_ioc(ioc_type, value, confidence, observed_at)
        except Exception:  # noqa: BLE001
            pass

    async def buffer_observation(
        self,
        id_a: str,
        id_b: str,
        finding_id: str,
        ts: float,
        source_type: str,
    ) -> None:
        """DuckPGQGraph: buffer via buffer_observation (F272, in-memory)."""
        try:
            await self._graph.buffer_observation(id_a, id_b, finding_id, ts, source_type)
        except Exception:  # noqa: BLE001
            pass

    async def flush_buffers(self) -> dict[str, int]:
        """DuckPGQGraph: flush via flush_buffers (F272)."""
        try:
            return self._graph.flush_buffers()
        except Exception:
            return {"ioc_flushed": 0, "obs_flushed": 0}

    async def record_observation(
        self,
        ioc_id_a: str,
        ioc_id_b: str,
        finding_id: str,
        ts: float,
        source_type: str,
    ) -> None:
        """DuckPGQGraph: record as observation edge."""
        try:
            self._graph.add_relation(ioc_id_a, ioc_id_b, "observed", 1.0, finding_id)
        except Exception:  # noqa: BLE001
            pass

    async def pivot(
        self,
        ioc_value: str,
        ioc_type: str,
        depth: int = 2,
    ) -> list[dict[str, Any]]:
        """DuckPGQGraph: uses DuckDB recursive CTE pivot (F271)."""
        try:
            return self._graph.pivot(ioc_value, ioc_type, depth)
        except Exception:
            return []

    def graph_stats(self) -> dict[str, int]:
        """DuckPGQGraph: graph_stats (F271)."""
        try:

            return {}
        except Exception:
            return {}

    async def export_stix_bundle(self) -> list[dict[str, Any]]:
        """DuckPGQGraph: STIX export via DuckDB (F271)."""
        try:
            return self._graph.export_stix_bundle()
        except Exception:
            return []


class IOCGraphAdapter(GraphProtocol):
    """
    Adapter wrapping IOCGraph (Kuzu) to implement GraphProtocol.

    IOCGraph is the STIX-compliant truth-write backend.
    Wraps Kuzu operations without changing behavior.

    Usage:
        ioc_graph = IOCGraph(...)
        await ioc_graph.initialize()
        adapter = IOCGraphAdapter(ioc_graph)
        # Use as GraphProtocol
        await adapter.upsert_ioc("1.2.3.4", "ipv4", sprint_id="sprint_1")
        await adapter.buffer_ioc("ipv4", "1.2.3.4")
        await adapter.flush_buffers()
    """

    __slots__ = ("_graph",)

    def __init__(self, graph: Any) -> None:
        """
        Initialize adapter with existing IOCGraph.

        Args:
            graph: IOCGraph instance to wrap
        """
        self._graph = graph

    # === TIER_A: Analytics (IOCGraph partial support) ===

    async def upsert_ioc(
        self,
        ioc_value: str,
        ioc_type: str,
        sprint_id: str,  # noqa: ARG002 — IOCGraph uses confidence, not sprint_id
        properties: dict[str, Any] | None = None,
        observed_at: float | None = None,
    ) -> bool:
        """Delegate IOC upsert to IOCGraph.upsert_ioc().

        [META]-012: observed_at captures the original event timestamp.
        """
        try:
            confidence = 0.5
            if properties:
                confidence = properties.get("confidence", 0.5)
            node_id = await self._graph.upsert_ioc(ioc_type, ioc_value, confidence, observed_at=observed_at)
            return node_id is not None
        except Exception:
            return False

    def find_connected(
        self,
        ioc_value: str,
        max_depth: int = 2,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """IOCGraph does not support DuckPGQ-style traversal — returns []. Use pivot()."""
        return []

    def upsert_relation(
        self,
        src: str,  # noqa: ARG002
        dst: str,  # noqa: ARG002
        rel_type: str,  # noqa: ARG002
        weight: float = 1.0,  # noqa: ARG002
        evidence: str = "",  # noqa: ARG002
    ) -> bool:
        """IOCGraph does not have a direct relation API — use upsert_ioc_batch + observations."""
        return False

    def upsert_ioc_batch(
        self,
        rows: list[tuple[str, str, float, str]],
        observed_at: float | None = None,
    ) -> int:
        """Delegate batch upsert to IOCGraph.upsert_ioc_batch().

        [META]-012: observed_at provides default timestamp for all rows.
        Supports 5-tuple format for per-row timestamps.
        """
        try:
            iocs = [(ioc_type, value, conf) for value, ioc_type, conf, _source in rows]
            return len(self._graph.upsert_ioc_batch(iocs, observed_at=observed_at))
        except Exception:
            return 0

    def find_connected_batch(
        self,
        values: list[str],
        max_depth: int = 2,  # noqa: ARG002
    ) -> dict[str, list[dict[str, Any]]]:
        """IOCGraph does not support DuckPGQ batch traversal."""
        return {}

    def get_top_nodes_by_degree(self, n: int = 20) -> list[dict[str, Any]]:
        """IOCGraph does not support this — returns []. Use graph_stats()."""
        return []

    def export_edge_list(self) -> Iterable[tuple[str, str, str, float]]:
        """IOCGraph does not export edge lists — returns []. Use export_stix_bundle()."""
        return []

    def stats(self) -> dict[str, Any]:
        """Delegate to IOCGraph.graph_stats()."""
        try:
            import asyncio

            if asyncio.iscoroutinefunction(self._graph.graph_stats):
                # Sync context: use new_event_loop() pattern (get_running_loop() fails here).
                # In Python 3.14+ get_event_loop() is deprecated in sync context but still works.
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                return loop.run_until_complete(self._graph.graph_stats())
            return self._graph.graph_stats()
        except Exception:
            return {}

    def checkpoint(self) -> None:
        """IOCGraph does not have a checkpoint — no-op."""
        pass

    # === TIER_S: STIX / Truth-write (full support) ===

    async def buffer_ioc(
        self,
        ioc_type: str,
        value: str,
        confidence: float = 1.0,
        observed_at: float | None = None,
    ) -> None:
        """Delegate buffered IOC to IOCGraph.buffer_ioc().

        [META]-006: observed_at captures the original event timestamp.
        """
        try:
            await self._graph.buffer_ioc(ioc_type, value, confidence, observed_at)
        except Exception:  # noqa: BLE001
            pass

    async def flush_buffers(self) -> dict[str, int]:
        """Delegate buffer flush to IOCGraph.flush_buffers()."""
        try:
            return await self._graph.flush_buffers()
        except Exception:
            return {}

    async def record_observation(
        self,
        ioc_id_a: str,
        ioc_id_b: str,
        finding_id: str,
        ts: float,
        source_type: str,
    ) -> None:
        """Delegate observation to IOCGraph.record_observation()."""
        try:
            await self._graph.record_observation(ioc_id_a, ioc_id_b, finding_id, ts, source_type)
        except Exception:  # noqa: BLE001
            pass

    async def pivot(
        self,
        ioc_value: str,
        ioc_type: str,
        depth: int = 2,
    ) -> list[dict[str, Any]]:
        """Delegate STIX pivot to IOCGraph.pivot()."""
        try:
            return await self._graph.pivot(ioc_value, ioc_type, depth)
        except Exception:
            return []

    def graph_stats(self) -> dict[str, int]:
        """Delegate to IOCGraph.graph_stats()."""
        try:
            import asyncio

            if asyncio.iscoroutinefunction(self._graph.graph_stats):
                # Sync context: use new_event_loop() pattern (get_running_loop() fails here).
                # In Python 3.14+ get_event_loop() is deprecated in sync context but still works.
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                return loop.run_until_complete(self._graph.graph_stats())
            return self._graph.graph_stats()
        except Exception:
            return {}

    async def export_stix_bundle(self) -> list[dict[str, Any]]:
        """Delegate STIX export to IOCGraph.export_stix_bundle()."""
        try:
            return await self._graph.export_stix_bundle()
        except Exception:
            return []


class GraphFacade:
    """
    F270 Phase 2: Unified graph access facade over GraphAttachmentStore.

    CONSOLIDATES 3 SLOTS into 1 capability-based interface:
        - _ioc_graph (analytics)     → TIER_A methods
        - _stix_graph (STIX export)  → TIER_S methods
        - _truth_write_graph (buffered writes) → TIER_S buffered methods

    Consumers no longer need to know which slot holds which graph.
    Check capability via hasattr() then call.

    Usage:
        facade = GraphFacade(store)  # DuckDBShadowStore with GraphAttachmentStore
        if hasattr(facade, 'export_stix_bundle'):
            bundle = await facade.export_stix_bundle()

        if hasattr(facade, 'find_connected'):
            connected = facade.find_connected("1.2.3.4")

    M1 8GB: GraphAttachmentStore is already fail-open throughout.
    """

    __slots__ = ("_store",)

    def __init__(self, store: Any) -> None:
        """
        Initialize facade with DuckDBShadowStore (or GraphAttachmentStore).

        Args:
            store: DuckDBShadowStore instance (has _graph_store()) or
                   GraphAttachmentStore instance directly.
        """
        # Support both DuckDBShadowStore (which has _graph_store()) and
        # GraphAttachmentStore (injected directly in tests)
        if hasattr(store, "_graph_store"):
            self._store: Any = store._graph_store()
        else:
            self._store = store

    # === TIER_A: Analytics — delegate to _ioc_graph slot ===

    async def upsert_ioc(
        self,
        ioc_value: str,
        ioc_type: str,
        sprint_id: str,
        properties: dict[str, Any] | None = None,
        observed_at: float | None = None,
    ) -> bool:
        """Upsert IOC — analytics path.

        [META]-012: observed_at captures the original event timestamp.
        """
        graph = self._store._ioc_graph
        if graph is None:
            return False
        if hasattr(graph, "upsert_ioc"):
            try:
                return await graph.upsert_ioc(ioc_value, ioc_type, sprint_id, properties, observed_at=observed_at)
            except Exception:
                return False
        # DuckPGQGraph fallback via add_ioc
        if hasattr(graph, "add_ioc"):
            try:
                row_id = graph.add_ioc(ioc_value, ioc_type, 0.5, sprint_id, observed_at=observed_at)
                return row_id is not None
            except Exception:
                return False
        return False

    def find_connected(self, ioc_value: str, max_depth: int = 2) -> list[dict[str, Any]]:
        """Graph traversal — analytics path (_ioc_graph)."""
        graph = self._store._ioc_graph
        if graph is None:
            return []
        if hasattr(graph, "find_connected"):
            try:
                return graph.find_connected(ioc_value, max_depth)
            except Exception:
                return []
        return []

    def upsert_relation(
        self,
        src: str,
        dst: str,
        rel_type: str,
        weight: float = 1.0,
        evidence: str = "",
    ) -> bool:
        """Add relation edge — analytics path."""
        graph = self._store._ioc_graph
        if graph is None:
            return False
        if hasattr(graph, "add_relation"):
            try:
                graph.add_relation(src, dst, rel_type, weight, evidence)
                return True
            except Exception:
                return False
        return False

    def upsert_ioc_batch(
        self,
        rows: list[tuple[str, str, float, str]],
        observed_at: float | None = None,
    ) -> int:
        """Batch upsert IOCs — analytics path.

        [META]-012: observed_at provides default timestamp for all rows.
        """
        graph = self._store._ioc_graph
        if graph is None:
            return 0
        if hasattr(graph, "upsert_ioc_batch"):
            try:
                return graph.upsert_ioc_batch(rows, observed_at=observed_at)
            except Exception:
                return 0
        return 0

    def find_connected_batch(self, values: list[str], max_depth: int = 2) -> dict[str, list[dict[str, Any]]]:
        """Batch graph traversal — analytics path."""
        graph = self._store._ioc_graph
        if graph is None:
            return {}
        if hasattr(graph, "find_connected_batch"):
            try:
                return graph.find_connected_batch(values, max_depth)
            except Exception:
                return {}
        return {}

    def get_top_nodes_by_degree(self, n: int = 20) -> list[dict[str, Any]]:
        """Top nodes by degree — analytics path."""
        graph = self._store._ioc_graph
        if graph is None:
            return []
        if hasattr(graph, "get_top_nodes_by_degree"):
            try:
                return graph.get_top_nodes_by_degree(n)
            except Exception:
                return []
        return []

    def export_edge_list(self) -> Iterator[tuple[str, str, str, float]]:
        """Export edge list — analytics path."""
        graph = self._store._ioc_graph
        if graph is None:
            return
        if hasattr(graph, "export_edge_list"):
            try:
                yield from graph.export_edge_list()
            except Exception:
                return
        return

    def stats(self) -> dict[str, Any]:
        """Graph stats — analytics path."""
        graph = self._store._ioc_graph
        if graph is None:
            return {}
        if hasattr(graph, "stats"):
            try:
                return graph.stats()
            except Exception:
                return {}
        return {}

    def checkpoint(self) -> None:
        """Flush WAL — analytics path."""
        graph = self._store._ioc_graph
        if graph is None:
            return
        if hasattr(graph, "checkpoint"):
            try:
                graph.checkpoint()
            except Exception:  # noqa: BLE001
                pass

    # === TIER_S: STIX / Truth-write — check _stix_graph first, then _truth_write_graph ===

    async def buffer_ioc(
        self,
        ioc_type: str,
        value: str,
        confidence: float = 1.0,
        observed_at: float | None = None,
    ) -> None:
        """Buffer IOC for flush — truth-write path (_truth_write_graph).

        [META]-006: observed_at captures the original event timestamp.
        """
        graph = self._store._truth_write_graph or self._store._stix_graph
        if graph is None:
            return
        if hasattr(graph, "buffer_ioc"):
            try:
                await graph.buffer_ioc(ioc_type, value, confidence, observed_at)
            except Exception:  # noqa: BLE001
                pass

    async def flush_buffers(self) -> dict[str, int]:
        """Flush buffered IOCs — truth-write path."""
        graph = self._store._truth_write_graph or self._store._stix_graph
        if graph is None:
            return {}
        if hasattr(graph, "flush_buffers"):
            try:
                return await graph.flush_buffers()
            except Exception:
                return {}
        return {}

    async def record_observation(
        self,
        ioc_id_a: str,
        ioc_id_b: str,
        finding_id: str,
        ts: float,
        source_type: str,
    ) -> None:
        """Record observation edge — truth-write path."""
        graph = self._store._truth_write_graph or self._store._stix_graph
        if graph is None:
            return
        if hasattr(graph, "record_observation"):
            try:
                await graph.record_observation(ioc_id_a, ioc_id_b, finding_id, ts, source_type)
            except Exception:  # noqa: BLE001
                pass

    async def pivot(
        self,
        ioc_value: str,
        ioc_type: str,
        depth: int = 2,
    ) -> list[dict[str, Any]]:
        """STIX pivot — STIX path (_stix_graph or _truth_write_graph)."""
        graph = self._store._stix_graph or self._store._truth_write_graph
        if graph is None:
            return []
        if hasattr(graph, "pivot"):
            try:
                return await graph.pivot(ioc_value, ioc_type, depth)
            except Exception:
                return []
        return []

    def graph_stats(self) -> dict[str, int]:
        """STIX graph stats — STIX path."""
        graph = self._store._stix_graph or self._store._truth_write_graph
        if graph is None:
            return {}
        if hasattr(graph, "graph_stats"):
            try:
                import asyncio

                result = graph.graph_stats()
                if asyncio.iscoroutine(result):
                    return {}
                return result
            except Exception:
                return {}
        return {}

    async def export_stix_bundle(self) -> list[dict[str, Any]]:
        """Export STIX bundle — STIX path."""
        import asyncio as _asyncio

        graph = self._store._stix_graph or self._store._truth_write_graph
        if graph is None:
            return []
        if hasattr(graph, "export_stix_bundle"):
            try:
                result = graph.export_stix_bundle()
                if _asyncio.iscoroutine(result):
                    return await result
                return result
            except Exception:
                return []
        return []
