"""
runtime/protocols/stix_protocol.py — F350M-R: STIX/TruthWrite Graph Protocol
=============================================================================

TIER_S: STIX-compliant truth-write operations for IOCGraph (Kuzu-backed).

IOCGraphAdapter is the primary implementation.
DuckPGQGraphAdapter: not supported (returns []/stubs for STIX methods).

GHOST_INVARIANTS:
- Fail-safe: all methods wrapped in try/except
- Bounded: entity/claim limits enforced by Kuzu
- Always-on: no feature flags
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StixProtocol(Protocol):
    """
    TIER_S: STIX/TruthWrite graph operations.

    Primary implementation: IOCGraphAdapter (wraps IOCGraph/Kuzu).
    DuckPGQGraphAdapter: not supported (returns []/stubs).

    Methods provide STIX 2.1 compliant entity and observation recording.
    """

    async def buffer_ioc(
        self,
        ioc_type: str,
        value: str,
        confidence: float = 1.0,
    ) -> None:
        """Buffer IOC for later flush. No-op on non-buffered backends."""
        ...

    async def flush_buffers(self) -> dict[str, int]:
        """Flush buffered IOCs. Returns {ioc_flushed, obs_flushed}."""
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
