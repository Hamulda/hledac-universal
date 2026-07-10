"""
runtime/protocols/graph_protocol.py — F270: Graph Interface v2

Unified protocol covering both DuckPGQGraph (analytics) and IOCGraph (STIX/truth-write).

GHOST_INVARIANTS:
- Fail-safe: upsert returns False on error, traversal returns [] on error
- Bounded: entity/claim limits enforced by underlying implementation
- Always-on: no feature flags for graph operations
"""



from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GraphProtocol(Protocol):
    """
    Unified entity graph operations protocol.

    Implementations:
        - DuckPGQGraphAdapter: DuckPGQGraph (DuckDB, analytics donor)
        - IOCGraphAdapter: IOCGraph (Kuzu, STIX/truth-write)

    Two capability tiers:
        TIER_A — Analytics (DuckPGQGraph):
            upsert_ioc, upsert_ioc_batch, upsert_relation,
            find_connected, find_connected_batch,
            get_top_nodes_by_degree, export_edge_list, stats, checkpoint

        TIER_S — STIX/TruthWrite (IOCGraph):
            buffer_ioc, flush_buffers (buffered writes),
            record_observation, pivot, graph_stats, export_stix_bundle

    Consumers check capability via hasattr() before calling tier-specific methods.
    """

    # === TIER_A: Analytics operations ===

    async def upsert_ioc(
        self,
        ioc_value: str,
        ioc_type: str,
        sprint_id: str,
        properties: dict[str, Any] | None = None,
    ) -> bool:
        """Upsert IOC into entity graph. Returns True on success."""
        ...

    def find_connected(
        self, ioc_value: str, max_depth: int = 2
    ) -> list[dict[str, Any]]:
        """Find connected entities via graph traversal. Returns [] on error."""
        ...

    def upsert_relation(
        self,
        src: str,
        dst: str,
        rel_type: str,
        weight: float = 1.0,
        evidence: str = "",
    ) -> bool:
        """Add relationship edge. Returns True on success."""
        ...

    def upsert_ioc_batch(
        self, rows: list[tuple[str, str, float, str]]
    ) -> int:
        """Batch upsert IOCs. Returns number of rows passed to backend."""
        ...

    def find_connected_batch(
        self, values: list[str], max_depth: int = 2
    ) -> dict[str, list[dict[str, Any]]]:
        """Batch graph traversal. Returns {} on error."""
        ...

    def get_top_nodes_by_degree(self, n: int = 20) -> list[dict[str, Any]]:
        """Return top N nodes by degree. Returns [] on error."""
        ...

    def export_edge_list(self) -> list[tuple[str, str, str, float]]:
        """Export all edges as (src, dst, rel_type, weight) tuples. Returns [] on error."""
        ...

    def stats(self) -> dict[str, Any]:
        """Return {nodes, edges, ...}. Returns {} on error."""
        ...

    def checkpoint(self) -> None:
        """Flush WAL to disk. Fail-safe (no-op on error)."""
        ...

    # === TIER_S: STIX / Truth-write operations (optional) ===

    async def buffer_ioc(
        self,
        ioc_type: str,
        value: str,
        confidence: float = 1.0,
    ) -> None:
        """Buffer IOC for later flush. No-op on non-buffered backends."""
        ...

    async def flush_buffers(self) -> dict[str, int]:
        """Flush buffered IOCs. Returns {}. No-op on non-buffered backends."""
        ...

    async def record_observation(
        self,
        ioc_id_a: str,
        ioc_id_b: str,
        finding_id: str,
        ts: float,
        source_type: str,
    ) -> None:
        """Record observation edge. No-op on non-STIX backends."""
        ...

    async def pivot(
        self,
        ioc_value: str,
        ioc_type: str,
        depth: int = 2,
    ) -> list[dict[str, Any]]:
        """STIX-compliant pivot. Returns [] on non-STIX backends."""
        ...

    def graph_stats(self) -> dict[str, int]:
        """STIX graph stats. Returns {} on non-STIX backends."""
        ...

    async def export_stix_bundle(self) -> list[dict[str, Any]]:
        """Export as STIX 2.1 bundle. Returns [] on non-STIX backends."""
        ...
