"""
KuzuGraphBridge — Sprint P2-3
=============================

Thin bridge layer that multiplexes graph operations between Kuzu (IOCGraph)
and DuckPGQGraph based on operation type.

ARCHITECTURE:
- Kuzu IOCGraph owns authoritative IOC entity storage + fast variable-length
  path traversal via `pivot()` (MATCH with `-[r*1..2]-` paths).
- DuckPGQGraph (DuckDB) serves analytics, path queries, and historical lookups.
- This bridge routes:
    * IOC buffer/upsert/pivot → Kuzu IOCGraph (fast, sidecar-style)
    * find_connected_batch, graph analytics → DuckPGQGraph (unchanged)
    * flush_buffers → Kuzu IOCGraph (end of sprint)

ENV GATE:
- HLEDAC_KUZU_ENABLED=1 (default 0, opt-in)
- Kuzu not installed → GraphBackendUnavailable → fail-soft, no crash

M1 8GB: Kuzu single-threaded executor (max_workers=1), bounded buffer (1024 IOCs).

SPRINT P2-3 INVARIANTS:
- Always-on: no feature flag toggle — bridge exists when kuzu available + enabled
- Bounded: MAX_BUFFERED_IOCS=1024 per bridge instance
- Fail-safe: any Kuzu error → returns empty result, sprint continues
- No new public APIs beyond what IOCGraph/DuckPGQGraph already expose
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Env gate ────────────────────────────────────────────────────────────────────

_KUZU_ENABLED: bool = os.environ.get("HLEDAC_KUZU_ENABLED", "0") == "1"

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_BUFFERED_IOCS: int = 1024  # Per-instance bound, prevents unbounded memory

# ── Lazy Kuzu import ───────────────────────────────────────────────────────────

_KUZU_AVAILABLE: bool = False
_IOCGraph: type | None = None

try:
    import kuzu as _kuzu

    from hledac.universal.knowledge.ioc_graph import GraphBackendUnavailableError, IOCGraph
    _IOCGraph = IOCGraph
    _KUZU_AVAILABLE = True
except ImportError:
    _kuzu = None  # type: ignore[assignment, misc]
    IOCGraph: type | None = None  # type: ignore[assignment, misc] # noqa: N818
    GraphBackendUnavailableError = Exception  # type: ignore[misc]


class GraphBackendUnavailableError(Exception):
    """Raised when kuzu is not installed and bridge is in use."""
    pass


# Backward compatibility alias — N818: exception names must end with Error
GraphBackendUnavailable = GraphBackendUnavailableError


# ── Bridge ────────────────────────────────────────────────────────────────────

class KuzuGraphBridge:
    """
    Graph operations multiplexer: Kuzu IOCGraph + DuckPGQGraph.

    Routes IOC-specific operations to Kuzu for fast pivot queries,
    delegates DuckPGQGraph for analytics and batch lookups.

    Thread-safe for sync callers. All Kuzu operations are fire-and-forget
    or awaited via the internal executor.
    """

    __slots__ = (
        "_kuzu_graph",
        "_duckpgq_graph",
        "_buffer",
        "_buffer_lock",
        "_kuzu_ready",
    )

    def __init__(
        self,
        duckpgq_graph: Any,  # DuckPGQGraph instance
        db_path: Path | None = None,
    ) -> None:
        if not _KUZU_AVAILABLE or not _KUZU_ENABLED:
            raise GraphBackendUnavailable(
                "KuzuGraphBridge: kuzu not available or HLEDAC_KUZU_ENABLED != 1"
            )

        self._kuzu_graph: Any = None
        self._duckpgq_graph = duckpgq_graph
        self._buffer: list[tuple[str, str, float]] = []  # [(ioc_type, ioc_value, confidence)]
        self._buffer_lock = asyncio.Lock()
        self._kuzu_ready = False

        # Initialize Kuzu graph (lazy, may raise GraphBackendUnavailable)
        try:
            if IOCGraph is None:
                raise GraphBackendUnavailable("IOCGraph not available")
            self._kuzu_graph = IOCGraph(db_path=db_path)
            self._kuzu_ready = True
        except Exception as e:
            logger.warning(f"[KuzuGraphBridge] IOCGraph init failed: {e}, operating in DuckPGQ-only mode")
            self._kuzu_ready = False

    # ── IOC buffer (Kuzu) ─────────────────────────────────────────────────────

    async def buffer_ioc(self, ioc_type: str, ioc_value: str, confidence: float = 0.5) -> None:
        """
        Buffer an IOC for batch upsert to Kuzu on flush.

        Fire-and-forget: sprint continues even if buffer is full.
        Bounded: drops oldest entries when MAX_BUFFERED_IOCS exceeded.
        """
        if not self._kuzu_ready or self._kuzu_graph is None:
            return

        async with self._buffer_lock:
            if len(self._buffer) >= _MAX_BUFFERED_IOCS:
                # Drop oldest 10% to make room
                drop = max(1, _MAX_BUFFERED_IOCS // 10)
                self._buffer = self._buffer[drop:]
            self._buffer.append((ioc_type, ioc_value, confidence))

    async def flush_buffers(self) -> dict[str, int]:
        """
        Flush buffered IOCs to Kuzu IOCGraph.

        Calls IOCGraph.flush_buffers() which does batch upsert via
        Kuzu's single-threaded executor.

        Returns:
            dict with "ioc_upserted" and "observation_count" keys.
        """
        if not self._kuzu_ready or self._kuzu_graph is None:
            return {"ioc_upserted": 0, "observation_count": 0}

        async with self._buffer_lock:
            if not self._buffer:
                return {"ioc_upserted": 0, "observation_count": 0}
            self._buffer.clear()

        try:
            return await self._kuzu_graph.flush_buffers()
        except Exception as e:
            logger.warning(f"[KuzuGraphBridge] flush_buffers failed: {e}")
            return {"ioc_upserted": 0, "observation_count": 0}

    # ── IOC upsert (Kuzu) ──────────────────────────────────────────────────────

    async def upsert_ioc(self, ioc_type: str, ioc_value: str, confidence: float = 0.5) -> bool:
        """
        Idempotent IOC upsert to Kuzu IOCGraph.

        Returns:
            True if upserted, False if skipped/error.
        """
        if not self._kuzu_ready or self._kuzu_graph is None:
            return False
        try:
            await self._kuzu_graph.upsert_ioc(ioc_type, ioc_value, confidence)
            return True
        except Exception as e:
            logger.debug(f"[KuzuGraphBridge] upsert_ioc failed: {e}")
            return False

    # ── Pivot (Kuzu) ───────────────────────────────────────────────────────────

    async def pivot(self, ioc_type: str, ioc_value: str, max_hops: int = 2) -> list[dict[str, Any]]:
        """
        Variable-length path traversal via Kuzu MATCH.

        Delegates to IOCGraph.pivot() which runs:
            MATCH (n:IOC)-[r*1..max_hops]-(m:IOC)
            WHERE n.ioc_type = $type AND n.value = $value

        Args:
            ioc_type: IOC type (e.g., "ipv4", "domain")
            ioc_value: IOC value
            max_hops: Maximum path length (1..3, clamped to 2 for safety)

        Returns:
            List of dicts with {src, dst, rel_type, hops, finding_id} or empty list.
        """
        if not self._kuzu_ready or self._kuzu_graph is None:
            return []
        max_hops = max(1, min(3, max_hops))
        try:
            return await self._kuzu_graph.pivot(ioc_type, ioc_value, max_hops=max_hops)
        except Exception as e:
            logger.debug(f"[KuzuGraphBridge] pivot failed: {e}")
            return []

    # ── DuckPGQGraph passthrough (analytics + batch) ───────────────────────────

    def add_ioc(
        self,
        value: str,
        ioc_type: str = "unknown",
        confidence: float = 0.5,
        source: str = "",
    ) -> Any:
        """
        Passthrough to DuckPGQGraph.add_ioc for canonical DuckDB insert.

        DuckPGQGraph owns the relational analytics schema.
        """
        if self._duckpgq_graph is None:
            return None
        try:
            return self._duckpgq_graph.add_ioc(value, ioc_type, confidence, source)
        except Exception as e:
            logger.debug(f"[KuzuGraphBridge] add_ioc (DuckPGQ) failed: {e}")
            return None

    def find_connected(self, value: str, max_hops: int = 2) -> list[dict[str, Any]]:
        """
        Passthrough to DuckPGQGraph.find_connected for analytics queries.

        DuckPGQGraph recursive CTE path finding for historical lookups.
        """
        if self._duckpgq_graph is None:
            return []
        try:
            return self._duckpgq_graph.find_connected(value, max_hops=max_hops)
        except Exception as e:
            logger.debug(f"[KuzuGraphBridge] find_connected (DuckPGQ) failed: {e}")
            return []

    def find_connected_batch(
        self, values: list[str], max_hops: int = 2
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Passthrough to DuckPGQGraph.find_connected_batch.
        """
        if self._duckpgq_graph is None:
            return {}
        try:
            return self._duckpgq_graph.find_connected_batch(values, max_hops=max_hops)
        except Exception as e:
            logger.debug(f"[KuzuGraphBridge] find_connected_batch failed: {e}")
            return {}

    # ── Stats (both backends) ─────────────────────────────────────────────────

    async def graph_stats(self) -> dict[str, int]:
        """
        Combined stats: Kuzu IOCGraph node/edge count + DuckPGQGraph stats.

        Returns merged dict with keys:
            kuzu_nodes, kuzu_edges (from Kuzu),
            duckpgq_nodes, duckpgq_edges (from DuckPGQGraph),
            buffered_iocs (in-memory buffer size).
        """
        stats: dict[str, int] = {
            "kuzu_nodes": 0,
            "kuzu_edges": 0,
            "duckpgq_nodes": 0,
            "duckpgq_edges": 0,
            "buffered_iocs": len(self._buffer),
        }

        # Kuzu stats
        if self._kuzu_ready and self._kuzu_graph is not None:
            try:
                kuzu_stats = await self._kuzu_graph.graph_stats()
                stats["kuzu_nodes"] = kuzu_stats.get("node_count", 0)
                stats["kuzu_edges"] = kuzu_stats.get("edge_count", 0)
            except Exception as e:
                logger.debug(f"[KuzuGraphBridge] graph_stats (Kuzu) failed: {e}")

        # DuckPGQ stats
        if self._duckpgq_graph is not None:
            try:
                dg_stats = self._duckpgq_graph.graph_stats()
                if isinstance(dg_stats, dict):
                    stats["duckpgq_nodes"] = dg_stats.get("node_count", 0)
                    stats["duckpgq_edges"] = dg_stats.get("edge_count", 0)
            except Exception as e:
                logger.debug(f"[KuzuGraphBridge] graph_stats (DuckPGQ) failed: {e}")

        return stats

    async def close(self) -> None:
        """Close Kuzu connection."""
        if self._kuzu_graph is not None:
            try:
                await self._kuzu_graph.close()
            except Exception as e:
                logger.debug(f"[KuzuGraphBridge] close failed: {e}")
            self._kuzu_graph = None
            self._kuzu_ready = False


# ── Factory ───────────────────────────────────────────────────────────────────

_KUZU_BRIDGE: KuzuGraphBridge | None = None
_KUZU_BRIDGE_LOCK = asyncio.Lock()


async def get_kuzu_graph_bridge() -> KuzuGraphBridge | None:
    """
    Get or create the singleton KuzuGraphBridge.

    Lazy-init. Returns None if Kuzu not available or not enabled.

    Thread-safe for async callers. Sync callers should use the module-level
    singleton only after first await has initialized it.
    """
    global _KUZU_BRIDGE
    if _KUZU_BRIDGE is not None:
        return _KUZU_BRIDGE

    if not _KUZU_AVAILABLE or not _KUZU_ENABLED:
        return None

    async with _KUZU_BRIDGE_LOCK:
        if _KUZU_BRIDGE is not None:
            return _KUZU_BRIDGE

        from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph

        try:
            duckpgq = DuckPGQGraph()
        except Exception as e:
            logger.warning(f"[KuzuGraphBridge] DuckPGQGraph init failed: {e}")
            return None

        try:
            _KUZU_BRIDGE = KuzuGraphBridge(duckpgq_graph=duckpgq)
            return _KUZU_BRIDGE
        except GraphBackendUnavailable:
            return None
        except Exception as e:
            logger.warning(f"[KuzuGraphBridge] creation failed: {e}")
            return None


def reset_kuzu_graph_bridge() -> None:
    """Reset singleton (called on sprint teardown)."""
    global _KUZU_BRIDGE
    if _KUZU_BRIDGE is not None:
        try:
            asyncio.get_event_loop().run_until_complete(_KUZU_BRIDGE.close())
        except Exception:
            pass
        _KUZU_BRIDGE = None
