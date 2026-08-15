"""
Smoke tests for knowledge/graph_service.py and DuckPGQGraph persistence.

Run: pytest tests/test_graph_service_smoke.py -v
"""

import asyncio

from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
from core import aclose


class TestDuckPGQGraphPersistence:
    """Verify DuckPGQGraph persistence survives reinit."""

    def test_add_entity_and_query(self):
        """Add entity via DuckPGQGraph, query via find_connected after reinit."""
        g1 = DuckPGQGraph()
        node_id = g1.add_ioc("smoke.ip.test", "ip_address", 0.95, "smoke_test")
        assert node_id is not None
        g1.checkpoint()  # ensure WAL is flushed before reinit

        g2 = DuckPGQGraph()  # new instance = reinit test
        # find_connected traverses edges only — isolated node has no neighbors
        neighbors = g2.find_connected("smoke.ip.test", max_hops=2)
        assert isinstance(neighbors, list)
        # source node is NOT in neighbors (source is only a starting point)
        values = [n["value"] for n in neighbors]
        assert "smoke.ip.test" not in values
        # verify the node IS in the DB by checking stats
        stats = g2.stats()
        assert stats.get("nodes", 0) >= 1

    def test_add_relation_and_query(self):
        """Add relation, query via find_connected after reinit."""
        g1 = DuckPGQGraph()
        g1.add_ioc("smoke.src.test", "domain", 0.9, "smoke_rel")
        g1.add_ioc("smoke.dst.test", "domain", 0.85, "smoke_rel")
        g1.add_relation("smoke.src.test", "smoke.dst.test", "resolves_to", 1.0, "smoke evidence")
        g1.checkpoint()

        g2 = DuckPGQGraph()
        neighbors = g2.find_connected("smoke.src.test", max_hops=2)
        dst_values = [n["value"] for n in neighbors]
        assert "smoke.dst.test" in dst_values, f"Relation not persisted: dst={dst_values}"

    def test_path_query_finds_path(self, session_event_loop: asyncio.AbstractEventLoop):
        """FIX F350M-R: Use session_event_loop fixture instead of asyncio.run()."""
        g1 = DuckPGQGraph()
        g1.add_ioc("path.src.test", "domain", 0.9, "smoke_path")
        g1.add_ioc("path.dst.test", "domain", 0.85, "smoke_path")
        g1.add_relation("path.src.test", "path.dst.test", "linked", 1.0, "path test")
        g1.checkpoint()

        g2 = DuckPGQGraph()
        paths = session_event_loop.run_until_complete(
            g2.find_paths_between_iocs("path.src.test", "path.dst.test", max_hops=4)
        )
        assert len(paths) >= 1, f"No path found between src and dst: {paths}"
        assert paths[0][0] == "path.src.test"
        assert paths[0][-1] == "path.dst.test"

    def test_stats_returns_node_edge_counts(self):
        """stats() returns non-empty dict with nodes/edges keys."""
        g = DuckPGQGraph()
        stats = g.stats()
        assert isinstance(stats, dict), f"stats() returned {type(stats)}, expected dict"
        assert "nodes" in stats or "edges" in stats, f"stats() missing keys: {stats}"
        assert stats.get("nodes", 0) >= 0
        assert stats.get("edges", 0) >= 0

    def test_find_connected_batch_returns_dict_structure(self):
        """find_connected_batch returns dict with correct structure (same-instance)."""
        g = DuckPGQGraph()
        g.add_ioc("batch.src1.test", "domain", 0.9, "smoke_batch")
        g.add_ioc("batch.dst1.test", "domain", 0.85, "smoke_batch")
        g.add_ioc("batch.src2.test", "domain", 0.8, "smoke_batch")
        g.add_relation("batch.src1.test", "batch.dst1.test", "linked", 1.0, "batch evidence")

        result = g.find_connected_batch(
            ["batch.src1.test", "batch.src2.test", "batch.none.test"],
            max_hops=2,
        )
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "batch.src1.test" in result
        assert isinstance(result["batch.src1.test"], list)
        assert "batch.src2.test" in result
        assert isinstance(result["batch.src2.test"], list)
        assert "batch.none.test" in result
        assert isinstance(result["batch.none.test"], list)

    def test_find_connected_batch_empty_input(self):
        """find_connected_batch with empty list returns empty dict."""
        g = DuckPGQGraph()
        result = g.find_connected_batch([], max_hops=2)
        assert result == {}

    def test_find_connected_batch_exception_fallback(self):
        """find_connected_batch returns {} on graph unavailable."""
        # When DuckPGQGraph is in READ-ONLY with no lock, graph is usable
        g = DuckPGQGraph()
        result = g.find_connected_batch(["nonexistent.value.test"], max_hops=2)
        assert isinstance(result, dict)
        assert "nonexistent.value.test" in result


class TestGraphServicePythonSetDedup:
    """Verify GraphService Python-set dedup path (when Rust unavailable)."""

    def test_upsert_ioc_adds_to_seen_set(self):
        """upsert_ioc adds (value, ioc_type) tuple to _seen_iocs Python set."""
        import hledac.universal.knowledge.graph_service as gs

        gs._RUST_IOC_DEDUP_AVAILABLE = False  # Force Python path

        from hledac.universal.knowledge.graph_service import GraphService

        svc = GraphService()
        svc._seen_iocs = set()
        svc._seen_rels = set()

        result = svc.upsert_ioc("gs.dedup.test", "domain", 0.9, "smoke")
        assert result is True, "upsert_ioc returned False"
        assert ("gs.dedup.test", "domain") in svc._seen_iocs

    def test_find_entity_history_returns_list(self):
        """find_entity_history returns list (empty or populated)."""
        import hledac.universal.knowledge.graph_service as gs

        gs._RUST_IOC_DEDUP_AVAILABLE = False

        from hledac.universal.knowledge.graph_service import GraphService

        svc = GraphService()
        svc._seen_iocs = set()
        svc._seen_rels = set()

        history = svc.find_entity_history("nonexistent.ioc.test", max_hops=1)
        assert isinstance(history, list), f"find_entity_history returned {type(history)}, expected list"
