"""
knowledge/graph/ — MOD-17: Dead code removed (F350M-R)

ARCHITECTURE (MOD-17):
  knowledge/graph/backend_protocol.py — DELETED (0 active callers)
  knowledge/graph/router.py — DELETED (0 active callers)

  Active graph layer:
  - DuckPGQGraph (graph/quantum_pathfinder.py) — primary graph backend
  - rustworkx.PyGraph (graph/graph_manager.py) — visualization only
  - rust_extensions::graph_traverse — optional acceleration for batch traversal

  All graph operations go through knowledge/graph_service.py which uses
  DuckPGQGraph directly — no routing needed.
"""

__all__ = []
