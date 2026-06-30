"""
Sprint 8VY: Shell Boundary Cleanup — Private Graph Slot Access Removed

Tests lock the following invariants:
1. __main__.py no longer accesses store._ioc_graph directly for stats/connected
2. _windup_synthesis() uses store.get_analytics_graph_for_synthesis() seam, not store._ioc_graph
3. New seam methods are fail-open: get_graph_stats() → {}, get_connected_iocs() → []
4. store is NOT graph authority — seams are thin read-only adapters
5. No new graph framework — only narrow seam methods added
6. analytics donor path remains explicitly donor-only
"""

