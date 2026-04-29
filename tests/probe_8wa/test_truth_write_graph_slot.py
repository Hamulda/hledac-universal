"""
Sprint 8WA: Truth-Write Graph Attachment Role Split — probe tests

Tests lock the following invariants:
1. _truth_write_graph is independent of _ioc_graph (analytics/donor) and _stix_graph (STIX)
2. inject_truth_write_graph() sets _truth_write_graph, clears _graph_attachment_kind
3. truth_write_graph_supports_buffered_writes() is dedicated to truth-write slot
4. _graph_ingest_findings uses _truth_write_graph, not _ioc_graph
5. aclose() flushes/closes _truth_write_graph separately from _ioc_graph
6. get_truth_write_graph() returns the injected truth-write graph
7. DuckPGQGraph must NEVER be injected into _truth_write_graph slot
8. No new graph framework — three separate slots remain explicit
"""

from __future__ import annotations

import asyncio

from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore


class MockTruthWriteGraph:
    """Mock graph with full buffered-write capability (simulates IOCGraph/Kuzu)."""

    def __init__(self):
        self.closed = False
        self.flush_called = False
        self.close_called = False
        self.ioc_buffer: list = []

    def buffer_ioc(self, ioc_type, value, confidence):
        self.ioc_buffer.append((ioc_type, value, confidence))

    async def buffer_ioc_async(self, ioc_type, value, confidence):
        self.ioc_buffer.append((ioc_type, value, confidence))

    async def flush_buffers(self):
        self.flush_called = True
        return {"ioc_flushed": len(self.ioc_buffer), "obs_flushed": 0}

    async def close(self):
        self.close_called = True
        self.closed = True


class MockAnalyticsGraph:
    """Mock donor backend (simulates DuckPGQGraph) — no buffered write methods."""

    def __init__(self):
        self.closed = False
        self.close_called = False

    async def close(self):
        self.close_called = True
        self.closed = True


class MockSTIXGraph:
    """Mock STIX-only graph (simulates IOCGraph with export_stix_bundle)."""

    def __init__(self):
        self.closed = False
        self.stix_exported = False

    async def export_stix_bundle(self):
        self.stix_exported = True
        return []

    async def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Slot independence tests
# ---------------------------------------------------------------------------


def test_truth_write_slot_independent_of_ioc_graph():
    """Injecting truth_write_graph does NOT affect _ioc_graph slot."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._ioc_graph = MockAnalyticsGraph()
    store._stix_graph = None
    store._truth_write_graph = None

    truth_graph = MockTruthWriteGraph()
    store.inject_truth_write_graph(truth_graph)

    assert store._ioc_graph is not None, "_ioc_graph was cleared"
    assert store._ioc_graph.__class__.__name__ == "MockAnalyticsGraph"
    assert store._truth_write_graph is truth_graph
    print("PASS: truth_write_graph injection does not affect _ioc_graph slot")


def test_truth_write_slot_independent_of_stix_graph():
    """Injecting truth_write_graph does NOT affect _stix_graph slot."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._stix_graph = MockSTIXGraph()
    store._truth_write_graph = None

    truth_graph = MockTruthWriteGraph()
    store.inject_truth_write_graph(truth_graph)

    assert store._stix_graph is not None, "_stix_graph was cleared"
    assert store._truth_write_graph is truth_graph
    print("PASS: truth_write_graph injection does not affect _stix_graph slot")


def test_ioc_graph_independent_of_truth_write():
    """Injecting analytics _ioc_graph does NOT affect _truth_write_graph slot."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._truth_write_graph = MockTruthWriteGraph()
    store._ioc_graph = None

    analytics_graph = MockAnalyticsGraph()
    store.inject_graph(analytics_graph)

    assert store._truth_write_graph is not None, "_truth_write_graph was cleared"
    assert store._ioc_graph is analytics_graph
    print("PASS: inject_graph does not affect _truth_write_graph slot")


def test_stix_graph_independent_of_truth_write():
    """Injecting _stix_graph does NOT affect _truth_write_graph slot."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._truth_write_graph = MockTruthWriteGraph()
    store._stix_graph = None

    stix_graph = MockSTIXGraph()
    store.inject_stix_graph(stix_graph)

    assert store._truth_write_graph is not None, "_truth_write_graph was cleared"
    assert store._stix_graph is stix_graph
    print("PASS: inject_stix_graph does not affect _truth_write_graph slot")


# ---------------------------------------------------------------------------
# Capability check tests
# ---------------------------------------------------------------------------


def test_truth_write_graph_supports_buffered_writes_true_for_bufferable():
    """IOCGraph mock → truth_write_graph_supports_buffered_writes() returns True."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._truth_write_graph = MockTruthWriteGraph()

    result = store.truth_write_graph_supports_buffered_writes()
    assert result is True, f"Expected True, got {result}"
    print("PASS: IOCGraph mock → truth_write_graph_supports_buffered_writes = True")


def test_truth_write_graph_supports_buffered_writes_false_for_analytics():
    """DuckPGQGraph mock → truth_write_graph_supports_buffered_writes() returns False."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._truth_write_graph = MockAnalyticsGraph()

    result = store.truth_write_graph_supports_buffered_writes()
    assert result is False, f"Expected False, got {result}"
    print("PASS: DuckPGQGraph mock → truth_write_graph_supports_buffered_writes = False")


def test_truth_write_graph_supports_buffered_writes_false_when_none():
    """No graph attached → truth_write_graph_supports_buffered_writes() returns False."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._truth_write_graph = None

    result = store.truth_write_graph_supports_buffered_writes()
    assert result is False, f"Expected False, got {result}"
    print("PASS: None graph → truth_write_graph_supports_buffered_writes = False")


def test_truth_write_graph_supports_buffered_writes_partial_false():
    """Graph with only buffer_ioc (no flush_buffers) → returns False."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)

    class PartialGraph:
        def buffer_ioc(self, *args):
            pass

    store._truth_write_graph = PartialGraph()

    result = store.truth_write_graph_supports_buffered_writes()
    assert result is False, f"Expected False, got {result}"
    print("PASS: Partial graph (buffer_ioc only) → False")


# ---------------------------------------------------------------------------
# inject_truth_write_graph tests
# ---------------------------------------------------------------------------


def test_inject_truth_write_graph_sets_slot():
    """inject_truth_write_graph() correctly sets _truth_write_graph."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._truth_write_graph = None

    graph = MockTruthWriteGraph()
    store.inject_truth_write_graph(graph)

    assert store._truth_write_graph is graph
    print("PASS: inject_truth_write_graph sets _truth_write_graph")


def test_inject_truth_write_graph_none_clears():
    """inject_truth_write_graph(None) clears _truth_write_graph."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._truth_write_graph = MockTruthWriteGraph()

    store.inject_truth_write_graph(None)

    assert store._truth_write_graph is None
    print("PASS: inject_truth_write_graph(None) clears slot")


def test_get_truth_write_graph_returns_injected():
    """get_truth_write_graph() returns the injected graph."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._truth_write_graph = None

    graph = MockTruthWriteGraph()
    store.inject_truth_write_graph(graph)

    assert store.get_truth_write_graph() is graph
    print("PASS: get_truth_write_graph() returns injected graph")


def test_get_truth_write_graph_returns_none_when_empty():
    """get_truth_write_graph() returns None when no graph injected."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._truth_write_graph = None

    assert store.get_truth_write_graph() is None
    print("PASS: get_truth_write_graph() returns None when empty")


# ---------------------------------------------------------------------------
# _graph_ingest_findings uses truth_write_graph tests
# ---------------------------------------------------------------------------


async def _run_graph_ingest_test(store, findings):
    """Helper to run _graph_ingest_findings with proper async context."""
    store._graph_ingest_findings(findings)
    await asyncio.sleep(0.1)  # allow bg task to run


def test_graph_ingest_findings_uses_truth_write_graph():
    """_graph_ingest_findings calls buffer_ioc on _truth_write_graph, not _ioc_graph."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)

    truth_graph = MockTruthWriteGraph()
    analytics_graph = MockAnalyticsGraph()

    store._truth_write_graph = truth_graph
    store._ioc_graph = analytics_graph  # Should NOT be used
    store._bg_tasks = set()

    # Use public IP (8.8.8.8) so extract_iocs_from_text finds it
    class MinimalFinding:
        finding_id = "test-1"
        payload_text = "8.8.8.8"
        source_type = "test"
        ts = 123456.0
        pattern_matches: list = []

    findings = [MinimalFinding()]

    asyncio.run(_run_graph_ingest_test(store, findings))

    assert len(truth_graph.ioc_buffer) > 0, (
        f"Expected buffer_ioc calls on _truth_write_graph, got {len(truth_graph.ioc_buffer)}"
    )
    print("PASS: _graph_ingest_findings calls buffer_ioc on _truth_write_graph")


def test_graph_ingest_findings_skips_when_no_truth_write_graph():
    """_graph_ingest_findings returns early when _truth_write_graph is None."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._truth_write_graph = None
    store._ioc_graph = MockTruthWriteGraph()  # Should NOT be used
    store._bg_tasks = set()

    class MinimalFinding:
        finding_id = "test-1"
        payload_text = "example.com"
        source_type = "test"
        ts = 123456.0
        pattern_matches: list = []

    findings = [MinimalFinding()]

    async def run_test():
        store._graph_ingest_findings(findings)

    asyncio.run(run_test())

    # No graph to call — should return immediately, no bg task created
    assert len(store._bg_tasks) == 0
    print("PASS: _graph_ingest_findings skips when _truth_write_graph is None")


# ---------------------------------------------------------------------------
# aclose tests
# ---------------------------------------------------------------------------


async def _do_aclose(store):
    """Helper that properly initializes store before aclose."""
    # Ensure required attributes that aclose checks
    if not hasattr(store, "_semantic_store"):
        store._semantic_store = None
    if not hasattr(store, "_wal_lmdb"):
        store._wal_lmdb = None
    await store.aclose()


def test_aclose_flushes_truth_write_graph():
    """aclose() calls flush_buffers on _truth_write_graph if present."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    tw_graph = MockTruthWriteGraph()
    store._truth_write_graph = tw_graph
    store._ioc_graph = None
    store._stix_graph = None
    store._bg_tasks = set()
    store._closed = False
    store._semantic_store = None
    store._wal_lmdb = None

    asyncio.run(_do_aclose(store))

    # aclose() sets _truth_write_graph = None after flush/close, so check the
    # local reference we saved BEFORE calling aclose()
    assert tw_graph.flush_called is True, (
        "flush_buffers not called on _truth_write_graph"
    )
    assert tw_graph.close_called is True, (
        "close not called on _truth_write_graph"
    )
    print("PASS: aclose() flushes and closes _truth_write_graph")


def test_aclose_closes_analytics_graph_separately():
    """aclose() closes _ioc_graph (analytics) separately from _truth_write_graph."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    tw_graph = MockTruthWriteGraph()
    analytics_graph = MockAnalyticsGraph()
    store._truth_write_graph = tw_graph
    store._ioc_graph = analytics_graph
    store._stix_graph = None
    store._bg_tasks = set()
    store._closed = False
    store._semantic_store = None
    store._wal_lmdb = None

    asyncio.run(_do_aclose(store))

    # aclose() sets _truth_write_graph = None after flush/close, so check the
    # local references we saved BEFORE calling aclose()
    assert tw_graph.close_called is True
    assert analytics_graph.close_called is True, (
        "close not called on _ioc_graph analytics"
    )
    print("PASS: aclose() closes both _truth_write_graph and _ioc_graph")


# ---------------------------------------------------------------------------
# Structured degradation remains explicit
# ---------------------------------------------------------------------------


def test_three_slots_are_fully_independent():
    """All three slots (_truth_write_graph, _ioc_graph, _stix_graph) are fully independent."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)

    tw_graph = MockTruthWriteGraph()
    analytics_graph = MockAnalyticsGraph()
    stix_graph = MockSTIXGraph()

    store.inject_truth_write_graph(tw_graph)
    store.inject_graph(analytics_graph)
    store.inject_stix_graph(stix_graph)

    assert store._truth_write_graph is tw_graph
    assert store._ioc_graph is analytics_graph
    assert store._stix_graph is stix_graph

    # Each has different capability
    assert store.truth_write_graph_supports_buffered_writes() is True
    assert store.graph_supports_buffered_writes() is False  # analytics has no buffers
    print("PASS: all three slots fully independent with distinct capabilities")


def test_docstrings_contain_trust_write_only_contract():
    """inject_truth_write_graph docstring explicitly states TRUTH-WRITE ONLY."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    doc = store.inject_truth_write_graph.__doc__ or ""
    assert "TRUTH-WRITE ONLY" in doc, f"Missing TRUTH-WRITE ONLY in docstring: {doc}"
    print("PASS: docstring contains TRUTH-WRITE ONLY contract")


def test_no_new_graph_framework():
    """Verify no GraphProtocol, generic get_graph(), or abstraction layer was added."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    public_attrs = [a for a in dir(store) if not a.startswith("_")]
    graph_attrs = [a for a in public_attrs if "graph" in a.lower()]

    # Allowed: the three dedicated slots + their injectors + seams from 8VY/8TF
    expected = {
        "inject_graph",
        "inject_stix_graph",
        "inject_truth_write_graph",
        "get_stix_graph",
        "get_truth_write_graph",
        "get_graph_attachment_kind",
        "graph_supports_buffered_writes",
        "truth_write_graph_supports_buffered_writes",
        # Sprint 8VY: analytics read-only seams
        "get_graph_stats",
        "get_connected_iocs",
        "get_analytics_graph_for_synthesis",
        "get_top_seed_nodes",
        "get_top_entities_for_ghost_global",
        # Sprint F193A §1: graph context annotation seam
        "annotate_findings_with_graph_context",
    }

    unexpected = [a for a in graph_attrs if a not in expected]
    assert len(unexpected) == 0, f"Unexpected graph methods found (new framework?): {unexpected}"
    print("PASS: no new graph framework — only three dedicated slots remain")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        # Slot independence
        test_truth_write_slot_independent_of_ioc_graph,
        test_truth_write_slot_independent_of_stix_graph,
        test_ioc_graph_independent_of_truth_write,
        test_stix_graph_independent_of_truth_write,
        # Capability checks
        test_truth_write_graph_supports_buffered_writes_true_for_bufferable,
        test_truth_write_graph_supports_buffered_writes_false_for_analytics,
        test_truth_write_graph_supports_buffered_writes_false_when_none,
        test_truth_write_graph_supports_buffered_writes_partial_false,
        # inject/get
        test_inject_truth_write_graph_sets_slot,
        test_inject_truth_write_graph_none_clears,
        test_get_truth_write_graph_returns_injected,
        test_get_truth_write_graph_returns_none_when_empty,
        # _graph_ingest_findings
        test_graph_ingest_findings_uses_truth_write_graph,
        test_graph_ingest_findings_skips_when_no_truth_write_graph,
        # aclose
        test_aclose_flushes_truth_write_graph,
        test_aclose_closes_analytics_graph_separately,
        # Structured degradation
        test_three_slots_are_fully_independent,
        test_docstrings_contain_trust_write_only_contract,
        test_no_new_graph_framework,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR: {t.__name__}: {e}")
            failed += 1

    print(f"\n{passed}/{passed+failed} passed")
    if failed:
        exit(1)
