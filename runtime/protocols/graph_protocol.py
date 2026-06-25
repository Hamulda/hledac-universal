"""
runtime/protocols/graph_protocol.py — F270: Graph Interface
=========================================================

Protocol for DuckPGQ entity graph operations.
Extracted from SprintScheduler's GRAPH group (~2 attributes).

GHOST_INVARIANTS:
- Fail-safe: upsert returns False on error
- Bounded: entity/claim limits enforced
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GraphProtocol(Protocol):
    """
    Entity graph operations protocol.

    Implementations:
        - DuckPGQGraphAdapter: wraps DuckPGQGraph

    Key methods:
        - upsert_ioc: entity/claim upsert
        - find_connected: graph traversal
    """

    async def upsert_ioc(
        self,
        ioc_value: str,
        ioc_type: str,
        sprint_id: str,
        properties: dict[str, Any] | None = None,
    ) -> bool:
        """Upsert IOC into entity graph."""
        ...

    def find_connected(
        self, ioc_value: str, max_depth: int = 2
    ) -> list[dict[str, Any]]:
        """Find connected entities via DuckPGQ traversal."""
        ...
