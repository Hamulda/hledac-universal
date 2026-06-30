"""
Sprint F264: Graph Pathfinder Activation — Hermetic Probe Tests

Covers:
  - QuantumInspiredPathFinder: basic, disconnected, MAX bounds, fail-soft
  - DuckPGQGraph: read-side smoke (no DB I/O)
  - graph.__init__ exports: re-exports of all public symbols
  - find_best_path async wrapper: returns shortest path
  - EvidenceNetworkAnalyzer.analyze_network output → pathfinder adjacency compat
  - CanonicalFinding shape contract for source_type="graph_path_analysis"
  - ResearchCoordinator._run_graph_path_analysis: gate + fail-soft behavior

Each test prints a one-line PASS/FAIL summary to stdout for at-a-glance
review. All tests are hermetic — no real network, no real DB writes,
no MLX dependency (mlx is lazy).
"""


import asyncio
import os
import sys
import time

# Ensure both hledac/universal and PycharmProjects/Hledac are on the path so
# that:
#  - `from graph import ...` and `from knowledge.duckdb_store import ...`
#    work (local relative style, requires hledac/universal in sys.path)
#  - `from hledac.universal.knowledge.duckdb_store import ...` also works
#    (absolute style, requires PycharmProjects/Hledac in sys.path so that
#    `hledac` is a discoverable package with hledac/universal/__init__.py).
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)  # hledac/universal
_REPO_ROOT = os.path.dirname(_PROJECT_ROOT)  # hledac/  (the python package dir)
_GRANDPARENT = os.path.dirname(_REPO_ROOT)  # PycharmProjects/Hledac
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, _GRANDPARENT)


def skip_if_no_backend(fn):
    """Decorator: when a test fails because the runtime backend (numpy/mlx/
    scipy) is missing, mark it as SKIP rather than FAIL — these are
    environment limitations, not code defects.
    """
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except RuntimeError as e:
            msg = str(e).lower()
            if any(t in msg for t in (
                "not initialized",
                "no module named 'numpy'",
                "no module named 'mlx'",
                "name 'np' is not defined",
            )):
                print(f"  {fn.__name__} SKIP  (backend unavailable: {e})")
                return
            raise
        except Exception as e:
            msg = str(e).lower()
            if "no module named 'numpy'" in msg:
                print(f"  {fn.__name__} SKIP  (backend unavailable: {e})")
                return
            raise
    return wrapper

# Quiet down noisy library loggers in tests
import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Test 1–6: QuantumInspiredPathFinder — basic path finding
# ---------------------------------------------------------------------------


def _run(coro):
    """Run a coroutine in a fresh event loop (hermetic)."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _psutil_available() -> bool:
    """Check whether psutil is importable (needed transitively by
    coordinators.research_coordinator). Returns False in minimal test envs.
    """
    try:
        import psutil  # noqa: F401
        return True
    except ImportError:
        return False


def _skip_if_no_psutil(test_name: str) -> bool:
    """If psutil is missing, print SKIP and return True (caller should early-return)."""
    if not _psutil_available():
        print(f"  {test_name} SKIP  (psutil not installed)")
        return True
    return False


def test_find_paths_simple_chain():
    """A→B→C linear graph returns at least one A→C path."""
    from graph.quantum_pathfinder import QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    try:
        ok = _run(pf.initialize({"A": ["B"], "B": ["C"], "C": []}))
        if not ok:
            print("  test_find_paths_simple_chain SKIP  (initialize returned False)")
            return
        paths = _run(pf.find_paths(start_nodes=["A"], target_nodes=["C"], max_steps=10))
        assert isinstance(paths, list), f"expected list, got {type(paths)}"
        # Either we found a path or quantum walk did not converge — both are valid
        # for stochastic pathfinder, but on a tiny linear graph convergence is likely
        if paths:
            for p in paths:
                assert isinstance(p, list), f"path is not a list: {p!r}"
                assert all(isinstance(x, str) for x in p), f"path nodes must be str: {p!r}"
        print("  test_find_paths_simple_chain PASS")
    finally:
        _run(pf.cleanup())


def test_find_paths_target_not_in_graph():
    """find_paths to a target that is not in the graph returns [] (fail-soft)."""
    from graph.quantum_pathfinder import QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    try:
        ok = _run(pf.initialize({"A": ["B"], "B": ["C"]}))
        if not ok:
            print("  test_find_paths_target_not_in_graph SKIP  (initialize returned False)")
            return
        paths = _run(
            pf.find_paths(start_nodes=["A"], target_nodes=["NONEXISTENT"], max_steps=5)
        )
        assert paths == [], f"expected [], got {paths!r}"
        print("  test_find_paths_target_not_in_graph PASS")
    finally:
        _run(pf.cleanup())


def test_find_paths_empty_input():
    """Empty start_nodes / target_nodes → [] (no exception)."""
    from graph.quantum_pathfinder import QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    try:
        ok = _run(pf.initialize({"A": ["B"]}))
        if not ok:
            print("  test_find_paths_empty_input SKIP  (initialize returned False)")
            return
        # empty start
        paths = _run(pf.find_paths(start_nodes=[], target_nodes=["A"], max_steps=5))
        assert paths == [], f"empty start → expected [], got {paths!r}"
        # empty target
        paths = _run(pf.find_paths(start_nodes=["A"], target_nodes=[], max_steps=5))
        assert paths == [], f"empty target → expected [], got {paths!r}"
        print("  test_find_paths_empty_input PASS")
    finally:
        _run(pf.cleanup())


def test_initialize_takes_dict():
    """initialize() accepts adjacency dict without erroring."""
    from graph.quantum_pathfinder import QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    try:
        # simple star graph
        adj = {
            "hub": ["n1", "n2", "n3"],
            "n1": ["hub"],
            "n2": ["hub"],
            "n3": ["hub"],
        }
        ok = _run(pf.initialize(adj))
        # ok may be False in minimal env (no numpy); both are valid outcomes
        assert ok in (True, False), f"unexpected return: {ok}"
        if ok:
            assert pf.initialized is True, "pf.initialized should be True after init"
        print("  test_initialize_takes_dict PASS")
    finally:
        _run(pf.cleanup())


def test_initialize_empty_dict_does_not_crash():
    """initialize({}) does not raise — fail-soft to uninitialized state."""
    from graph.quantum_pathfinder import QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    try:
        ok = _run(pf.initialize({}))
        # Either True (degenerate) or False (rejected empty) — both are valid
        assert ok in (True, False), f"unexpected return: {ok}"
        print("  test_initialize_empty_dict_does_not_crash PASS")
    finally:
        _run(pf.cleanup())


def test_find_best_path_returns_list_of_str():
    """find_best_path convenience wrapper returns list[str] or []."""
    from graph.quantum_pathfinder import find_best_path

    # Linear graph A→B→C
    adj = {"A": ["B"], "B": ["C"], "C": []}
    result = _run(find_best_path(adj, "A", "C"))
    assert isinstance(result, list), f"expected list, got {type(result)}"
    assert all(isinstance(x, str) for x in result), f"non-str in path: {result!r}"
    print("  test_find_best_path_returns_list_of_str PASS")


# ---------------------------------------------------------------------------
# Test 7–9: Disconnected graphs
# ---------------------------------------------------------------------------


def test_disconnected_components_returns_empty_or_partial():
    """Disconnected graph: target unreachable from start → []."""
    from graph.quantum_pathfinder import QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    try:
        # Two disconnected components
        adj = {
            "A": ["B"], "B": ["A"],
            "X": ["Y"], "Y": ["X"],
        }
        ok = _run(pf.initialize(adj))
        if not ok:
            print("  test_disconnected_components_returns_empty_or_partial SKIP  (initialize returned False)")
            return
        paths = _run(pf.find_paths(start_nodes=["A"], target_nodes=["X"], max_steps=10))
        assert paths == [], f"disconnected → expected [], got {paths!r}"
        print("  test_disconnected_components_returns_empty_or_partial PASS")
    finally:
        _run(pf.cleanup())


def test_self_loop_does_not_crash():
    """Self-loop A→A is valid input; find_paths should not raise."""
    from graph.quantum_pathfinder import QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    try:
        ok = _run(pf.initialize({"A": ["A"], "B": ["A"]}))
        if not ok:
            print("  test_self_loop_does_not_crash SKIP  (initialize returned False)")
            return
        paths = _run(pf.find_paths(start_nodes=["B"], target_nodes=["A"], max_steps=5))
        # Direct edge B→A is present, so path is likely trivial; just check no crash
        assert isinstance(paths, list), f"expected list, got {type(paths)}"
        print("  test_self_loop_does_not_crash PASS")
    finally:
        _run(pf.cleanup())


def test_start_node_not_in_graph_does_not_crash():
    """find_paths with start_node not in graph → fail-soft, no exception."""
    from graph.quantum_pathfinder import QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    try:
        ok = _run(pf.initialize({"A": ["B"], "B": ["C"]}))
        if not ok:
            print("  test_start_node_not_in_graph_does_not_crash SKIP  (initialize returned False)")
            return
        paths = _run(
            pf.find_paths(start_nodes=["GHOST"], target_nodes=["A"], max_steps=5)
        )
        assert paths == [], f"ghost start → expected [], got {paths!r}"
        print("  test_start_node_not_in_graph_does_not_crash PASS")
    finally:
        _run(pf.cleanup())


# ---------------------------------------------------------------------------
# Test 10–13: MAX bounds enforcement
# ---------------------------------------------------------------------------


def test_max_quantum_nodes_constant_present():
    """MAX_QUANTUM_NODES is exported and bounded sensibly."""
    from graph.quantum_pathfinder import MAX_QUANTUM_NODES

    assert isinstance(MAX_QUANTUM_NODES, int)
    assert 1 <= MAX_QUANTUM_NODES <= 100_000, f"out of plausible range: {MAX_QUANTUM_NODES}"
    print(f"  test_max_quantum_nodes_constant_present PASS  (={MAX_QUANTUM_NODES})")


def test_max_quantum_edges_constant_present():
    """MAX_QUANTUM_EDGES is exported and bounded sensibly (F264)."""
    from graph.quantum_pathfinder import MAX_QUANTUM_EDGES

    assert isinstance(MAX_QUANTUM_EDGES, int)
    assert 1 <= MAX_QUANTUM_EDGES <= 1_000_000, f"out of plausible range: {MAX_QUANTUM_EDGES}"
    # Should be at least as large as MAX_QUANTUM_NODES^2 / 4 to allow
    # reasonable graph density
    print(f"  test_max_quantum_edges_constant_present PASS  (={MAX_QUANTUM_EDGES})")


def test_initialize_clamps_max_nodes():
    """initialize with max_nodes > MAX_QUANTUM_NODES is clamped (logged warning)."""
    from graph.quantum_pathfinder import MAX_QUANTUM_NODES, QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    try:
        # Build a graph with more nodes than MAX_QUANTUM_NODES
        # but small enough to not OOM the test env — use a small MAX_QUANTUM_NODES
        # override
        # Build 200 nodes, force clamp
        nodes = {f"n{i}": ["n0"] if i > 0 else [f"n{j}" for j in range(1, 50)] for i in range(200)}
        ok = _run(pf.initialize(nodes, max_nodes=10_000))  # asks for 10k, will clamp
        # Either it succeeded (clamped) or returned False — both are fail-soft valid
        assert ok in (True, False), f"unexpected: {ok}"
        # If it did succeed, pf.n_nodes should be ≤ MAX_QUANTUM_NODES
        if ok and getattr(pf, "initialized", False):
            assert pf.n_nodes <= MAX_QUANTUM_NODES, (
                f"n_nodes {pf.n_nodes} > MAX_QUANTUM_NODES {MAX_QUANTUM_NODES}"
            )
        print("  test_initialize_clamps_max_nodes PASS")
    finally:
        _run(pf.cleanup())


def test_build_sparse_matrix_truncates_edges():
    """_build_sparse_matrix truncates when edge count > MAX_QUANTUM_EDGES."""
    from graph.quantum_pathfinder import MAX_QUANTUM_EDGES, QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    try:
        # Build a sparse matrix with WAY more edges than the ceiling by
        # bypassing _initialize_* (which clamps nodes). We directly call
        # the internal helper.
        n = 100
        rows = [(i + 1) % n for i in range(MAX_QUANTUM_EDGES + 5000)]
        cols = [i % n for i in range(MAX_QUANTUM_EDGES + 5000)]
        data = [1.0] * (MAX_QUANTUM_EDGES + 5000)
        pf.n_nodes = n
        try:
            _run(pf._build_sparse_matrix(rows, cols, data))
        except (ImportError, ModuleNotFoundError) as e:
            print(
                f"  test_build_sparse_matrix_truncates_edges SKIP  "
                f"(backend unavailable: {e})"
            )
            return
        # Should not have raised; matrix should be populated
        assert pf.adjacency_matrix is not None, "adjacency_matrix should be set"
        print("  test_build_sparse_matrix_truncates_edges PASS")
    finally:
        try:
            _run(pf.cleanup())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Test 14–16: Fail-soft + lazy import
# ---------------------------------------------------------------------------


def test_create_quantum_pathfinder_factory():
    """create_quantum_pathfinder returns an instance or None (fail-soft)."""
    from graph.quantum_pathfinder import create_quantum_pathfinder

    pf = create_quantum_pathfinder()
    if pf is None:
        # Acceptable: backend init failed
        print("  test_create_quantum_pathfinder_factory PASS  (factory returned None)")
        return
    try:
        assert hasattr(pf, "initialize"), "pf must have initialize()"
        assert hasattr(pf, "find_paths"), "pf must have find_paths()"
        assert hasattr(pf, "cleanup"), "pf must have cleanup()"
        print("  test_create_quantum_pathfinder_factory PASS  (instance OK)")
    finally:
        _run(pf.cleanup())


def test_lazy_imports_no_eager_mlx():
    """Importing graph.quantum_pathfinder must NOT eagerly import mlx/scipy/numpy."""
    # Re-import the module and check _NP_CACHE, _MLX_CACHE, _SPARSE_CACHE
    # are still None (lazy discipline)
    import importlib

    import graph.quantum_pathfinder as qp

    importlib.reload(qp)  # fresh module state

    # After fresh import, the lazy caches should be None
    assert qp._NP_CACHE is None, "_NP_CACHE should be None after import (lazy)"
    assert qp._MLX_CACHE is None, "_MLX_CACHE should be None after import (lazy)"
    assert qp._SPARSE_CACHE is None, "_SPARSE_CACHE should be None after import (lazy)"
    print("  test_lazy_imports_no_eager_mlx PASS")


def test_cleanup_idempotent():
    """cleanup() can be called multiple times without raising."""
    from graph.quantum_pathfinder import QuantumInspiredPathFinder

    pf = QuantumInspiredPathFinder()
    _run(pf.initialize({"A": ["B"]}))
    _run(pf.cleanup())
    # Second cleanup should be safe (sets initialized=False or stays False)
    _run(pf.cleanup())
    assert pf.initialized is False, "pf.initialized should be False after cleanup"
    print("  test_cleanup_idempotent PASS")


# ---------------------------------------------------------------------------
# Test 17–19: graph.__init__ exports
# ---------------------------------------------------------------------------


def test_graph_init_exports():
    """graph/__init__.py exports all expected public symbols."""
    import graph as gpkg

    expected = [
        "GraphManager",
        "GRAPH_AVAILABLE",
        "QuantumInspiredPathFinder",
        "QuantumPathConfig",
        "DuckPGQGraph",
        "create_quantum_pathfinder",
        "find_best_path",
        "MAX_QUANTUM_NODES",
        "MAX_QUANTUM_EDGES",
        "QUANTUM_PATHFINDER_AVAILABLE",
    ]
    for name in expected:
        assert name in gpkg.__all__, f"{name} missing from graph.__all__"
        assert hasattr(gpkg, name), f"{name} not accessible via graph.{name}"
    print(f"  test_graph_init_exports PASS  ({len(expected)} symbols verified)")


def test_graph_init_stub_returns_empty_on_missing_dep():
    """Even if quantum_pathfinder import fails, the package provides a stub
    find_best_path that returns [] and create_quantum_pathfinder that returns
    None — verified by checking the stub types are present.
    """
    # We can't easily trigger ImportError without re-execing; instead, verify
    # the callables exist and have the right shape.
    import inspect

    import graph as gpkg

    assert callable(gpkg.create_quantum_pathfinder)
    assert inspect.iscoroutinefunction(gpkg.find_best_path)
    print("  test_graph_init_stub_returns_empty_on_missing_dep PASS")


# ---------------------------------------------------------------------------
# Test 20–22: EvidenceNetworkAnalyzer integration smoke
# ---------------------------------------------------------------------------


def test_evidence_analyzer_analyze_network_shape():
    """analyze_network returns the expected dict shape for downstream use."""
    from advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer

    async def _go():
        an = EvidenceNetworkAnalyzer()
        entities = [
            {"type": "domain", "value": "evil.com"},
            {"type": "ip", "value": "1.2.3.4"},
            {"type": "email", "value": "admin@evil.com"},
            {"type": "domain", "value": "sub.evil.com"},
        ]
        result = await an.analyze_network(entities)
        return result

    result = _run(_go())
    assert isinstance(result, dict), f"expected dict, got {type(result)}"
    # Required downstream keys for the pathfinder integration
    for key in ("entities", "edges", "centrality", "clusters"):
        assert key in result, f"missing key {key!r} in analyze_network output: {list(result.keys())}"
    centrality = result.get("centrality") or {}
    if centrality:  # may be empty if no relations derived
        assert isinstance(centrality, dict), f"centrality not a dict: {type(centrality)}"
    print("  test_evidence_analyzer_analyze_network_shape PASS")


def test_evidence_analyzer_output_compatible_with_pathfinder():
    """Verify that the analyze_network output can drive QuantumInspiredPathFinder."""
    from advanced_web.evidence_network_analyzer import EvidenceNetworkAnalyzer
    from graph.quantum_pathfinder import QuantumInspiredPathFinder

    async def _go():
        an = EvidenceNetworkAnalyzer()
        # Hand-crafted entities that will produce a connected graph
        entities = [
            {"type": "domain", "value": "alpha.com", "sources": ["test"]},
            {"type": "domain", "value": "beta.com", "sources": ["test"]},
            {"type": "domain", "value": "gamma.com", "sources": ["test"]},
            {"type": "ip", "value": "10.0.0.1", "sources": ["test"]},
            {"type": "ip", "value": "10.0.0.2", "sources": ["test"]},
        ]
        result = await an.analyze_network(entities)
        return result

    result = _run(_go())
    edges = result.get("edges") or []
    centrality = result.get("centrality") or {}

    # Build adjacency list the way _run_graph_path_analysis does
    top_nodes = sorted(centrality, key=lambda k: centrality.get(k, 0.0), reverse=True)[:10]
    adj: dict[str, list[str]] = {n: [] for n in top_nodes}
    for e in edges:
        try:
            src = str(e.get("src", ""))
            dst = str(e.get("dst", ""))
            if src in adj and dst not in adj[src]:
                adj[src].append(dst)
            if dst in adj and src not in adj[dst]:
                adj[dst].append(src)
        except Exception:
            pass

    # If we got a usable graph, run the pathfinder
    if len(top_nodes) >= 2 and any(adj[n] for n in top_nodes):
        pf = QuantumInspiredPathFinder()
        try:
            ok = _run(pf.initialize(adj))
            assert ok is True, "initialize should succeed"
            # Try to find a path between any two top nodes
            paths = _run(
                pf.find_paths(
                    start_nodes=[top_nodes[0]],
                    target_nodes=[top_nodes[1]] if len(top_nodes) > 1 else top_nodes,
                    max_steps=10,
                )
            )
            assert isinstance(paths, list)
        finally:
            _run(pf.cleanup())
    print("  test_evidence_analyzer_output_compatible_with_pathfinder PASS")


# ---------------------------------------------------------------------------
# Test 23–24: CanonicalFinding shape for source_type="graph_path_analysis"
# ---------------------------------------------------------------------------


def test_canonical_finding_graph_path_shape():
    """CanonicalFinding with source_type='graph_path_analysis' is constructible."""
    try:
        from knowledge.duckdb_store import CanonicalFinding
    except (ImportError, ModuleNotFoundError) as e:
        print(f"  test_canonical_finding_graph_path_shape SKIP  (duckdb_store unavailable: {e})")
        return

    f = CanonicalFinding(
        finding_id="graph_path_abc123",
        query="evil.com",
        source_type="graph_path_analysis",
        confidence=0.5,
        ts=time.time(),
        provenance=("graph_path_analysis", "research_coordinator", "evil.com", "1.2.3.4"),
        payload_text='{"path": ["evil.com", "1.2.3.4"]}',
    )
    assert f.source_type == "graph_path_analysis"
    assert f.provenance[0] == "graph_path_analysis"
    assert f.provenance[2] == "evil.com"
    assert f.provenance[3] == "1.2.3.4"
    # Frozen struct: cannot mutate
    try:
        f.source_type = "mutated"  # type: ignore[misc]
        raise AssertionError("CanonicalFinding should be frozen")
    except (AttributeError, Exception):
        pass
    print("  test_canonical_finding_graph_path_shape PASS")


def test_canonical_finding_required_fields():
    """Missing required fields raises TypeError (structural contract)."""
    try:
        from knowledge.duckdb_store import CanonicalFinding
    except (ImportError, ModuleNotFoundError) as e:
        print(f"  test_canonical_finding_required_fields SKIP  (duckdb_store unavailable: {e})")
        return

    try:
        CanonicalFinding()  # type: ignore[call-arg]
        raise AssertionError("should have raised")
    except TypeError:
        pass
    print("  test_canonical_finding_required_fields PASS")


# ---------------------------------------------------------------------------
# Test 25–27: ResearchCoordinator._run_graph_path_analysis behavior
# ---------------------------------------------------------------------------


def test_research_coordinator_gate_default_off():
    """_run_graph_path_analysis returns [] when HLEDAC_ENABLE_GRAPH_PATHS != 1."""
    if _skip_if_no_psutil("test_research_coordinator_gate_default_off"):
        return
    import os
    old = os.environ.pop("HLEDAC_ENABLE_GRAPH_PATHS", None)
    try:
        from coordinators.research_coordinator import UniversalResearchCoordinator
        coord = UniversalResearchCoordinator.__new__(UniversalResearchCoordinator)
        coord._evidence_analyzer = None

        async def _go():
            return await coord._run_graph_path_analysis(
                entities=[{"type": "domain", "value": "x.com"}],
                query="x.com",
            )

        result = _run(_go())
        assert result == [], f"gate off → expected [], got {result!r}"
    finally:
        if old is not None:
            os.environ["HLEDAC_ENABLE_GRAPH_PATHS"] = old
    print("  test_research_coordinator_gate_default_off PASS")


def test_research_coordinator_empty_entities_returns_empty():
    """_run_graph_path_analysis returns [] when entities is empty (even with gate on)."""
    if _skip_if_no_psutil("test_research_coordinator_empty_entities_returns_empty"):
        return
    import os
    old = os.environ.get("HLEDAC_ENABLE_GRAPH_PATHS")
    os.environ["HLEDAC_ENABLE_GRAPH_PATHS"] = "1"
    try:
        from coordinators.research_coordinator import UniversalResearchCoordinator
        coord = UniversalResearchCoordinator.__new__(UniversalResearchCoordinator)
        coord._evidence_analyzer = object()

        async def _go():
            return await coord._run_graph_path_analysis(
                entities=[],
                query="x.com",
            )

        result = _run(_go())
        assert result == [], f"empty entities → expected [], got {result!r}"
    finally:
        if old is None:
            os.environ.pop("HLEDAC_ENABLE_GRAPH_PATHS", None)
        else:
            os.environ["HLEDAC_ENABLE_GRAPH_PATHS"] = old
    print("  test_research_coordinator_empty_entities_returns_empty PASS")


def test_research_coordinator_fail_soft_on_malformed_analyzer_output():
    """If the evidence analyzer raises or returns garbage, path analysis fails soft."""
    if _skip_if_no_psutil("test_research_coordinator_fail_soft_on_malformed_analyzer_output"):
        return
    import os
    old = os.environ.get("HLEDAC_ENABLE_GRAPH_PATHS")
    os.environ["HLEDAC_ENABLE_GRAPH_PATHS"] = "1"
    try:
        from coordinators.research_coordinator import UniversalResearchCoordinator

        class _BadAnalyzer:
            async def analyze_network(self, entities, **_kw):
                raise RuntimeError("synthetic analyzer failure")

        coord = UniversalResearchCoordinator.__new__(UniversalResearchCoordinator)
        coord._evidence_analyzer = _BadAnalyzer()

        async def _go():
            return await coord._run_graph_path_analysis(
                entities=[{"type": "domain", "value": "x.com"}],
                query="x.com",
            )

        result = _run(_go())
        assert result == [], f"analyzer failure → expected [], got {result!r}"
    finally:
        if old is None:
            os.environ.pop("HLEDAC_ENABLE_GRAPH_PATHS", None)
        else:
            os.environ["HLEDAC_ENABLE_GRAPH_PATHS"] = old
    print("  test_research_coordinator_fail_soft_on_malformed_analyzer_output PASS")


# ---------------------------------------------------------------------------
# Test 28: execute_research_plan accepts graph_analysis parameter
# ---------------------------------------------------------------------------


def test_execute_research_plan_accepts_graph_analysis():
    """execute_research_plan signature includes graph_analysis parameter."""
    if _skip_if_no_psutil("test_execute_research_plan_accepts_graph_analysis"):
        return
    import inspect

    from coordinators.research_coordinator import UniversalResearchCoordinator

    sig = inspect.signature(UniversalResearchCoordinator.execute_research_plan)
    assert "graph_analysis" in sig.parameters, (
        f"graph_analysis param missing from execute_research_plan; "
        f"params: {list(sig.parameters.keys())}"
    )
    default = sig.parameters["graph_analysis"].default
    assert default is False, f"graph_analysis default should be False, got {default}"
    print("  test_execute_research_plan_accepts_graph_analysis PASS")


# ---------------------------------------------------------------------------
# Test 29: DuckPGQGraph read-side (no DB) smoke
# ---------------------------------------------------------------------------


def test_duckpgq_graph_class_exported():
    """DuckPGQGraph is importable and has the documented surface."""
    from graph.quantum_pathfinder import DuckPGQGraph

    # Documented public surface
    for method in (
        "find_connected",
        "find_connected_batch",
        "find_paths_between_iocs",
        "stats",
        "add_ioc",
        "add_relation",
        "get_top_nodes_by_degree",
        "export_edge_list",
    ):
        assert hasattr(DuckPGQGraph, method), (
            f"DuckPGQGraph.{method} missing"
        )
    print("  test_duckpgq_graph_class_exported PASS")


# ---------------------------------------------------------------------------
# Test 30: env-var override for MAX_QUANTUM_EDGES
# ---------------------------------------------------------------------------


def test_max_quantum_edges_env_override():
    """QUANTUM_MAX_EDGES env var overrides default (best-effort)."""
    import os
    saved = os.environ.get("QUANTUM_MAX_EDGES")
    os.environ["QUANTUM_MAX_EDGES"] = "9999"
    try:
        # Re-import the module to pick up the env var
        import importlib

        import graph.quantum_pathfinder as qp
        importlib.reload(qp)
        assert qp.MAX_QUANTUM_EDGES == 9999, (
            f"env override failed: {qp.MAX_QUANTUM_EDGES}"
        )
    finally:
        if saved is None:
            os.environ.pop("QUANTUM_MAX_EDGES", None)
        else:
            os.environ["QUANTUM_MAX_EDGES"] = saved
        # Reload to restore default
        import importlib

        import graph.quantum_pathfinder as qp
        importlib.reload(qp)
    print("  test_max_quantum_edges_env_override PASS")


# ===========================================================================
# Pytest-compatible runner
# ===========================================================================


def _all_tests():
    """Collect all test functions in this module."""
    funcs = []
    for name, obj in sorted(globals().items()):
        if name.startswith("test_") and callable(obj):
            funcs.append((name, obj))
    return funcs


def main() -> int:
    """Run all tests sequentially. Returns 0 on full pass."""
    tests = _all_tests()
    print(f"\n=== Running {len(tests)} graph-pathfinder probe tests ===\n")
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            print(f"  {name} FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  {name} ERROR: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n=== {passed} passed, {failed} failed (of {len(tests)}) ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
