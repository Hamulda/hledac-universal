"""
runtime/protocols/analytics_protocol.py — F350M-R: Analytics Graph Protocol
============================================================================

TIER_A: Analytics operations for DuckPGQGraph (DuckDB-backed).

DuckPGQGraphAdapter is the primary implementation.

GHOST_INVARIANTS:
- Fail-safe: traversal returns [] on error
- Bounded: entity/claim limits enforced by DuckDB
- Always-on: no feature flags
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AnalyticsProtocol(Protocol):
    """
    TIER_A: Analytics graph operations.

    Primary implementation: DuckPGQGraphAdapter (wraps DuckPGQGraph).
    IOCGraphAdapter: not supported (returns []/False/stubs).

    Methods mirror DuckPGQGraph's analytics capabilities.
    """

    async def upsert_ioc(
        self,
        ioc_value: str,
        ioc_type: str,
        sprint_id: str,
        properties: dict[str, Any] | None = None,
        observed_at: float | None = None,
    ) -> bool:
        """Upsert IOC into analytics graph. Returns True on success.

        [META]-012: observed_at captures the original event timestamp.
        """
        ...

    def find_connected(self, ioc_value: str, max_depth: int = 2) -> list[dict[str, Any]]:
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
        self,
        rows: list[tuple[str, str, float, str]],
        observed_at: float | None = None,
    ) -> int:
        """Batch upsert IOCs. Returns number of rows passed to backend.

        [META]-012: observed_at provides default timestamp for all rows.
        """
        ...

    def find_connected_batch(self, values: list[str], max_depth: int = 2) -> dict[str, list[dict[str, Any]]]:
        """Batch graph traversal. Returns {} on error."""
        ...

    def get_top_nodes_by_degree(self, n: int = 20) -> list[dict[str, Any]]:
        """Return top N nodes by degree. Returns [] on error."""
        ...

    def export_edge_list(self) -> Iterable[tuple[str, str, str, float]]:
        """Yield/export edges as (src, dst, rel_type, weight) tuples. Yields nothing on error."""
        ...

    def stats(self) -> dict[str, Any]:
        """Return {nodes, edges, ...}. Returns {} on error."""
        ...

    def checkpoint(self) -> None:
        """Flush WAL to disk. Fail-safe (no-op on error)."""
        ...
