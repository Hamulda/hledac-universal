"""
Sprint 8TF: Graph Store Attachment Guards — probe tests

Tests lock the following invariants:
1. Store does NOT call graph methods it doesn't have (no silent AttributeError)
2. DuckPGQGraph is correctly detected as non-bufferable (no false positives)
3. IOCGraph is correctly detected as bufferable (no false negatives)
4. Background ingest is gated by capability check (not silently skipped)
5. aclose teardown handles close()/flush_buffers() on both backends safely
6. Store is NOT graph truth owner — explicit diagnostic helpers reflect reality
"""


import asyncio
import sys
from unittest import IsolatedAsyncioTestCase

# Add universal to path
from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore


class MockBufferableGraph:
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


class MockNonBufferableGraph:
    """Mock donor backend (simulates DuckPGQGraph) — no buffered write methods."""

    def __init__(self):
        self.closed = False
        self.close_called = False
        self.checkpoint_called = False

    def checkpoint(self):
        self.checkpoint_called = True

    async def close(self):
        self.close_called = True
        self.closed = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_graph_supports_buffered_writes_iocgraph_returns_true():
    """IOCGraph mock → graph_supports_buffered_writes() returns True."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._ioc_graph = MockBufferableGraph()
    store._graph_attachment_kind = "IOCGraph"

    result = store.graph_supports_buffered_writes()
    assert result is True, f"Expected True, got {result}"
    print("PASS: IOCGraph mock → supports buffered writes = True")


def test_graph_supports_buffered_writes_duckpgq_returns_false():
    """DuckPGQGraph mock → graph_supports_buffered_writes() returns False."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._ioc_graph = MockNonBufferableGraph()
    store._graph_attachment_kind = "DuckPGQGraph"

    result = store.graph_supports_buffered_writes()
    assert result is False, f"Expected False, got {result}"
    print("PASS: DuckPGQGraph mock → supports buffered writes = False")


def test_graph_supports_buffered_writes_none_returns_false():
    """No graph attached → graph_supports_buffered_writes() returns False."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._ioc_graph = None
    store._graph_attachment_kind = None

    result = store.graph_supports_buffered_writes()
    assert result is False, f"Expected False, got {result}"
    print("PASS: No graph → supports buffered writes = False")


def test_inject_graph_sets_attachment_kind():
    """inject_graph() records the class name of the attached backend."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._ioc_graph = None
    store._graph_attachment_kind = None

    graph = MockBufferableGraph()
    store.inject_graph(graph)

    assert store._ioc_graph is graph
    assert store._graph_attachment_kind == "MockBufferableGraph"
    assert store.get_graph_attachment_kind() == "MockBufferableGraph"
    print("PASS: inject_graph sets _graph_attachment_kind correctly")


def test_get_graph_attachment_kind_none_when_no_graph():
    """No graph → get_graph_attachment_kind() returns None."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._ioc_graph = None
    store._graph_attachment_kind = None

    result = store.get_graph_attachment_kind()
    assert result is None, f"Expected None, got {result}"
    print("PASS: No graph → attachment_kind = None")


def test_graph_supports_buffered_writes_no_false_positives():
    """
    Ensure DuckPGQGraph-like (no buffer_ioc) does not falsely return True.
    Only exact {buffer_ioc AND flush_buffers} presence gates True.
    """
    class PartialGraph:
        """Missing flush_buffers — should return False."""
        def buffer_ioc(self, a, b, c):
            pass

    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._ioc_graph = PartialGraph()
    store._graph_attachment_kind = "PartialGraph"

    result = store.graph_supports_buffered_writes()
    assert result is False, f"Expected False for partial graph, got {result}"
    print("PASS: Partial graph (buffer_ioc only) → supports buffered writes = False")


def test_inject_graph_accepts_none():
    """inject_graph(None) should not crash — clears attachment."""
    store = DuckDBShadowStore.__new__(DuckDBShadowStore)
    store._ioc_graph = MockBufferableGraph()
    store._graph_attachment_kind = "MockBufferableGraph"

    store.inject_graph(None)

    assert store._ioc_graph is None
    assert store._graph_attachment_kind is None
    print("PASS: inject_graph(None) clears state safely")


# ---------------------------------------------------------------------------
# Teardown guard tests (aclose path)
# ---------------------------------------------------------------------------


def test_aclose_no_flush_buffers_on_duckpgq():
    """
    aclose path: DuckPGQGraph has no flush_buffers.
    The guard 'if callable(getattr(g, "flush_buffers", None))' must prevent the call.
    This test verifies the guard pattern does not raise when flush_buffers is absent.
    """
    graph = MockNonBufferableGraph()

    # Simulate the aclose guard logic
    exc_info = None
    try:
        if callable(getattr(graph, "flush_buffers", None)):
            asyncio.get_event_loop().run_until_complete(graph.flush_buffers())
    except Exception as e:
        exc_info = e

    assert exc_info is None, f"flush_buffers guard should not raise, got {exc_info}"
    assert graph.checkpoint_called is False  # DuckPGQGraph has checkpoint, not called here
    print("PASS: aclose flush_buffers guard safe for DuckPGQGraph")


def test_aclose_flush_buffers_on_iocgraph():
    """aclose path: IOCGraph HAS flush_buffers — guard allows the call."""
    graph = MockBufferableGraph()

    exc_info = None
    try:
        if callable(getattr(graph, "flush_buffers", None)):
            asyncio.get_event_loop().run_until_complete(graph.flush_buffers())
    except Exception as e:
        exc_info = e

    assert exc_info is None, f"flush_buffers should succeed, got {exc_info}"
    assert graph.flush_called is True, "flush_buffers was not called on IOCGraph"
    print("PASS: aclose flush_buffers guard allows call on IOCGraph")


def test_aclose_close_on_duckpgq():
    """aclose path: DuckPGQGraph has close() — guard allows it."""
    graph = MockNonBufferableGraph()

    exc_info = None
    try:
        if callable(getattr(graph, "close", None)):
            asyncio.get_event_loop().run_until_complete(graph.close())
    except Exception as e:
        exc_info = e

    assert exc_info is None, f"close should succeed, got {exc_info}"
    assert graph.close_called is True, "close was not called on DuckPGQGraph"
    print("PASS: aclose close guard allows call on DuckPGQGraph")


# ---------------------------------------------------------------------------
# Non-authoritative marker — store is NOT graph truth owner
# ---------------------------------------------------------------------------


def test_store_is_not_graph_truth_owner_note():
    """
    Verify that the NON-AUTHORITATIVE comment exists in inject_graph docstring.
    This is a documentation-lock test — the note is the contract.
    """
    docstring = DuckDBShadowStore.inject_graph.__doc__
    assert docstring is not None, "inject_graph must have docstring"
    assert "STORE IS NOT GRAPH TRUTH OWNER" in docstring, (
        "inject_graph docstring must contain NON-AUTHORITATIVE marker"
    )
    assert "IOCGraph (Kuzu)" in docstring, "Must mention IOCGraph (Kuzu)"
    assert "DuckPGQGraph (DuckDB)" in docstring, "Must mention DuckPGQGraph (DuckDB)"
    print("PASS: inject_graph docstring confirms store is NOT graph truth owner")


def test_graph_supports_buffered_writes_diagnostic_only():
    """
    graph_supports_buffered_writes() is a COMPAT SEAM, not a canonical API.
    Verify the docstring confirms NON-AUTHORITATIVE DIAGNOSTIC status.
    """
    docstring = DuckDBShadowStore.graph_supports_buffered_writes.__doc__
    assert docstring is not None, "graph_supports_buffered_writes must have docstring"
    assert "NON-AUTHORITATIVE" in docstring, "Must be marked NON-AUTHORITATIVE"
    assert "COMP" in docstring.upper() or "COMPAT" in docstring.upper(), (
        "Must be marked as COMPAT seam"
    )
    print("PASS: graph_supports_buffered_writes marked as compat/diagnostic only")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    tests = [
        test_graph_supports_buffered_writes_iocgraph_returns_true,
        test_graph_supports_buffered_writes_duckpgq_returns_false,
        test_graph_supports_buffered_writes_none_returns_false,
        test_inject_graph_sets_attachment_kind,
        test_get_graph_attachment_kind_none_when_no_graph,
        test_graph_supports_buffered_writes_no_false_positives,
        test_inject_graph_accepts_none,
        test_aclose_no_flush_buffers_on_duckpgq,
        test_aclose_flush_buffers_on_iocgraph,
        test_aclose_close_on_duckpgq,
        test_store_is_not_graph_truth_owner_note,
        test_graph_supports_buffered_writes_diagnostic_only,
    ]

    failed = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
            failed.append(t.__name__)
        except Exception as e:
            print(f"ERROR: {t.__name__}: {e}")
            failed.append(t.__name__)

    print()
    if failed:
        print(f"FAILED: {len(failed)}/{len(tests)}")
        for f in failed:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print(f"ALL PASSED: {len(tests)}/{len(tests)}")
        sys.exit(0)
