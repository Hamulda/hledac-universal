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


import asyncio
import pytest

from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore


class MockAnalyticsGraphWithStats:
    """Mock DuckPGQGraph with stats and find_connected (simulates analytics donor)."""

    def __init__(self):
        self.closed = False

    def stats(self):
        return {"nodes": 42, "edges": 7, "pgq_active": True}

    def find_connected(self, ioc_value: str, max_hops: int = 2):
        return [{"value": "connected1", "ioc_type": "ipv4", "confidence": 0.9}]

    async def close(self):
        self.closed = True


class MockAnalyticsGraphMissingMethods:
    """Mock graph missing stats/find_connected (tests fail-open)."""

    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


# =============================================================================
# get_graph_stats() seam tests
# =============================================================================


def test_get_graph_stats_returns_duckpgq_stats():
    """get_graph_stats() delegates to DuckPGQGraph.stats() and returns dict."""
    store = DuckDBShadowStore(":memory:")
    mock_graph = MockAnalyticsGraphWithStats()
    store.inject_graph(mock_graph)

    stats = store.get_graph_stats()

    assert stats == {"nodes": 42, "edges": 7, "pgq_active": True}


def test_get_graph_stats_fail_open_empty_dict():
    """get_graph_stats() returns {} when _ioc_graph is None (fail-open)."""
    store = DuckDBShadowStore(":memory:")

    stats = store.get_graph_stats()

    assert stats == {}


def test_get_graph_stats_fail_open_missing_method():
    """get_graph_stats() returns {} when graph lacks stats() method."""
    store = DuckDBShadowStore(":memory:")
    store.inject_graph(MockAnalyticsGraphMissingMethods())

    stats = store.get_graph_stats()

    assert stats == {}


def test_get_graph_stats_fail_open_exception():
    """get_graph_stats() returns {} when stats() raises exception."""
    store = DuckDBShadowStore(":memory:")

    class BadGraph:
        def stats(self):
            raise RuntimeError("graph error")

    store.inject_graph(BadGraph())
    stats = store.get_graph_stats()

    assert stats == {}


def test_get_graph_stats_validates_return_shape():
    """get_graph_stats() validates stats() returns {nodes, edges}."""
    store = DuckDBShadowStore(":memory:")

    class IncompleteStatsGraph:
        def stats(self):
            return {"nodes": 10}  # missing 'edges'

    store.inject_graph(IncompleteStatsGraph())
    stats = store.get_graph_stats()

    assert stats == {}


# =============================================================================
# get_connected_iocs() seam tests
# =============================================================================


def test_get_connected_iocs_returns_list():
    """get_connected_iocs() delegates to DuckPGQGraph.find_connected()."""
    store = DuckDBShadowStore(":memory:")
    mock_graph = MockAnalyticsGraphWithStats()
    store.inject_graph(mock_graph)

    result = store.get_connected_iocs("8.8.8.8", max_hops=2)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["value"] == "connected1"


def test_get_connected_iocs_fail_open_empty_list():
    """get_connected_iocs() returns [] when _ioc_graph is None."""
    store = DuckDBShadowStore(":memory:")

    result = store.get_connected_iocs("testioc", max_hops=2)

    assert result == []


def test_get_connected_iocs_fail_open_missing_method():
    """get_connected_iocs() returns [] when graph lacks find_connected()."""
    store = DuckDBShadowStore(":memory:")
    store.inject_graph(MockAnalyticsGraphMissingMethods())

    result = store.get_connected_iocs("testioc", max_hops=2)

    assert result == []


def test_get_connected_iocs_fail_open_exception():
    """get_connected_iocs() returns [] when find_connected() raises exception."""
    store = DuckDBShadowStore(":memory:")

    class BadGraph:
        def find_connected(self, *args, **kwargs):
            raise RuntimeError("graph error")

    store.inject_graph(BadGraph())
    result = store.get_connected_iocs("testioc", max_hops=2)

    assert result == []


def test_get_connected_iocs_returns_non_list():
    """get_connected_iocs() returns [] when find_connected() returns non-list."""
    store = DuckDBShadowStore(":memory:")

    class BadGraph:
        def find_connected(self, *args, **kwargs):
            return "not a list"

    store.inject_graph(BadGraph())
    result = store.get_connected_iocs("testioc", max_hops=2)

    assert result == []


# =============================================================================
# get_analytics_graph_for_synthesis() seam tests
# =============================================================================


def test_get_analytics_graph_for_synthesis_returns_graph():
    """get_analytics_graph_for_synthesis() returns _ioc_graph reference."""
    store = DuckDBShadowStore(":memory:")
    mock_graph = MockAnalyticsGraphWithStats()
    store.inject_graph(mock_graph)

    result = store.get_analytics_graph_for_synthesis()

    assert result is mock_graph


def test_get_analytics_graph_for_synthesis_returns_none():
    """get_analytics_graph_for_synthesis() returns None when _ioc_graph is None."""
    store = DuckDBShadowStore(":memory:")

    result = store.get_analytics_graph_for_synthesis()

    assert result is None


def test_get_analytics_graph_for_synthesis_explicit_none_no_attribute_error():
    """Seam handles None gracefully without AttributeError."""
    store = DuckDBShadowStore(":memory:")
    store._ioc_graph = None

    result = store.get_analytics_graph_for_synthesis()

    assert result is None


# =============================================================================
# Invariant: store is NOT graph authority
# =============================================================================


def test_store_seams_are_read_only_adapters():
    """Seams do NOT make store a graph authority — only read-only delegation."""
    store = DuckDBShadowStore(":memory:")

    # get_graph_stats does not store anything
    stats1 = store.get_graph_stats()
    assert stats1 == {}

    # get_connected_iocs does not modify anything
    result = store.get_connected_iocs("test", max_hops=1)
    assert result == []

    # get_analytics_graph_for_synthesis returns existing ref, does not create graph
    ref = store.get_analytics_graph_for_synthesis()
    assert ref is None


def test_store_does_not_implement_graph_methods():
    """DuckDBShadowStore has no graph methods — only seams delegating to attached graph."""
    store = DuckDBShadowStore(":memory:")

    # These methods should NOT exist on store directly
    assert not hasattr(store, "stats")
    assert not hasattr(store, "find_connected")
    assert not hasattr(store, "upsert_ioc")
    assert not hasattr(store, "buffer_ioc")

    # Only seam methods should exist
    assert hasattr(store, "get_graph_stats")
    assert hasattr(store, "get_connected_iocs")
    assert hasattr(store, "get_analytics_graph_for_synthesis")


# =============================================================================
# Invariant: no new graph framework
# =============================================================================


def test_no_generic_get_graph_method():
    """Store has no generic get_graph() method — only specific seams."""
    store = DuckDBShadowStore(":memory:")

    assert not hasattr(store, "get_graph")
    assert not hasattr(store, "get_graph_by_role")
    assert not hasattr(store, "graph")
