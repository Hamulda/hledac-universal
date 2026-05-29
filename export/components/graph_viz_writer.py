# hledac/universal/export/components/graph_viz_writer.py
# Sprint F11N: Streaming graph viz export
"""
Streaming graph visualization section writer.
Yields Mermaid diagram chunks — bounded by MAX_NODES/MAX_EDGES, fail-soft.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

__all__ = ["stream_graph_viz_section", "GraphVizSection"]


class GraphVizSection:
    """Result of graph rendering — keeps node/edge counts for telemetry."""

    def __init__(
        self,
        node_count: int = 0,
        edge_count: int = 0,
        capped: bool = False,
    ) -> None:
        self.node_count = node_count
        self.edge_count = edge_count
        self.capped = capped


async def stream_graph_viz_section(
    graph_manager: object,
    *,
    max_nodes: int = 200,
    max_edges: int = 400,
) -> AsyncGenerator[str]:
    """
    Stream graph visualization as a Mermaid flowchart section.

    Yields sections:
      1. Section header with node/edge counts
      2. Mermaid flowchart definition (nodes + edges)
      3. Capped warning if truncated

    Parameters
    ----------
    graph_manager : object
        Must expose: get_nodes(), get_edges(), node_count, edge_count
        Falls back to duckdb_store.get_ioc_graph() if not structured.
    max_nodes : int
        Hard cap on rendered nodes (Mermaid performance).
    max_edges : int
        Hard cap on rendered edges.
    """
    # ── Probe graph manager interface ─────────────────────────────────
    try:
        nodes = getattr(graph_manager, "nodes", None) or getattr(graph_manager, "get_nodes", lambda: [])()
    except Exception:
        nodes = []
    try:
        edges = getattr(graph_manager, "edges", None) or getattr(graph_manager, "get_edges", lambda: [])()
    except Exception:
        edges = []

    node_count = len(nodes) if nodes else 0
    edge_count = len(edges) if edges else 0
    capped = node_count > max_nodes or edge_count > max_edges

    # ── Header ─────────────────────────────────────────────────────────
    yield "# Graph Visualization\n\n"
    yield f"_Nodes: {node_count} | Edges: {edge_count}"
    if capped:
        yield f" (cap: {max_nodes} nodes / {max_edges} edges)"
    yield "\n\n"

    if not nodes and not edges:
        yield "_No graph data available._\n"
        return

    # ── Mermaid flowchart ─────────────────────────────────────────────
    yield "```mermaid\nflowchart TD\n"

    # Nodes
    rendered_nodes: set = set()
    for node in (nodes or [])[:max_nodes]:
        if isinstance(node, dict):
            node_id = node.get("id", node.get("ioc_value", "unnamed"))
            label = node.get("label", node.get("ioc_value", node.get("type", "?")))
            _node_type = node.get("type", "ioc")
        else:
            node_id = str(node)
            label = str(node)

        safe_id = node_id.replace("-", "_").replace(" ", "_")[:40]
        if safe_id not in rendered_nodes:
            yield f"    {safe_id}[{label}]\n"
            rendered_nodes.add(safe_id)

    # Edges
    rendered_edges = 0
    for edge in (edges or [])[:max_edges]:
        if isinstance(edge, dict):
            src = str(edge.get("source", edge.get("src", "?")))
            dst = str(edge.get("target", edge.get("dst", "?")))
            rel = edge.get("relation", edge.get("type", "related_to"))
        else:
            src, dst, rel = str(edge), "?", "related_to"

        safe_src = src.replace("-", "_").replace(" ", "_")[:40]
        safe_dst = dst.replace("-", "_").replace(" ", "_")[:40]

        # Skip self-loops in Mermaid
        if safe_src == safe_dst:
            continue

        # Mermaid relation labels can't have spaces nicely — truncate
        rel_safe = rel.replace(" ", "-")[:20] if rel else "related"
        yield f"    {safe_src} -->|{rel_safe}| {safe_dst}\n"
        rendered_edges += 1

        if rendered_edges % 50 == 0:
            await asyncio.sleep(0)  # yield periodically

    yield "```\n"

    if capped:
        yield f"\n_Warning: graph capped at {max_nodes} nodes / {max_edges} edges._\n"
