"""
test_f_graph_serde.py — pickleless graph persistence regression tests.

What these tests cover
----------------------
1. ``intelligence._graph_serde`` round-trips a NetworkX graph via
   JSON+orjson (no Python ``pickle`` interpreter).
2. Legacy ``.pkl`` files (Python pickle) are still loadable as a one-shot
   migration, but ONLY on F196B-safe paths. Outside graphs dir = rejected.
3. igraph ``write_picklez`` / ``Graph.Load`` path is unchanged.
4. Bound: ``MAX_NODES`` pruning on write AND read.
5. The 5 ``pickle`` references that remain in
   ``intelligence/relationship_discovery.py`` are *only* lazy + F196B-guarded
   legacy-migration sites — never write path.

Why these tests exist
---------------------
CLAUDE.md invariants:
  * No bare ``pickle.load`` on user/IO-controlled data (F196B).
  * M1 8GB friendly: zero-copy, orjson > pickle.
  * ``asyncio.run`` invariant: no nested event loop.

Sprint companion to F-Bloom-Regression — verify pickle-free hot path.
"""

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_graphs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``~/.hledac/graphs`` to a tmp dir for F196B-safe tests."""
    fake_home = tmp_path / "hledac_home"
    fake_home.mkdir()
    # Must mirror the F196B path used inside _graph_serde / relationship_discovery
    # — i.e. ``~/.hledac/graphs`` (with the dot).
    graphs_dir = fake_home / ".hledac" / "graphs"
    graphs_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    return graphs_dir


@pytest.fixture
def tiny_nx_graph():
    """A small NetworkX graph for round-trip tests."""
    nx = pytest.importorskip("networkx")
    g = nx.Graph()
    g.add_node("a", kind="domain")
    g.add_node("b", kind="ip")
    g.add_node("c", kind="asn")
    g.add_edge("a", "b", weight=0.9, rel_type="resolves_to")
    g.add_edge("b", "c", weight=0.5, rel_type="owned_by")
    return g


# ---------------------------------------------------------------------------
# Test: round-trip via JSON+orjson
# ---------------------------------------------------------------------------


class TestGraphSerdeJsonRoundtrip:
    def test_save_and_load_roundtrip(self, tmp_graphs_dir: Path, tiny_nx_graph) -> None:
        from hledac.universal.recon._graph_serde import load_nx_graph_jsonl, save_nx_graph_jsonl

        out = tmp_graphs_dir / "graph.jsonl"
        ok = save_nx_graph_jsonl(str(out), tiny_nx_graph, max_nodes=10_000)
        assert ok is True
        assert out.exists()
        # Magic header check
        with open(out, "rb") as f:
            head = f.read(32)
        assert b"_hledac_graph_v" in head, "must write our JSON envelope"

        loaded = load_nx_graph_jsonl(str(out), max_nodes=10_000)
        assert loaded is not None
        assert loaded.number_of_nodes() == 3
        assert loaded.number_of_edges() == 2
        # Edge attributes preserved
        a_to_b = loaded.get_edge_data("a", "b")
        assert a_to_b is not None
        assert a_to_b.get("weight") == 0.9

    def test_our_format_detector(self, tmp_graphs_dir: Path, tiny_nx_graph) -> None:
        from hledac.universal.recon._graph_serde import (
            is_our_format,
            save_nx_graph_jsonl,
        )

        out = tmp_graphs_dir / "g.jsonl"
        save_nx_graph_jsonl(str(out), tiny_nx_graph, max_nodes=10_000)
        assert is_our_format(str(out)) is True
        # Foreign file -> False
        foreign = tmp_graphs_dir / "foreign.json"
        foreign.write_bytes(b'{"unrelated": true}')
        assert is_our_format(str(foreign)) is False
        # Missing file -> False
        assert is_our_format(str(tmp_graphs_dir / "missing")) is False


# ---------------------------------------------------------------------------
# Test: bound (MAX_NODES prune)
# ---------------------------------------------------------------------------


class TestGraphSerdeBound:
    def test_prune_on_write_when_over_max(self, tmp_graphs_dir: Path) -> None:
        nx = pytest.importorskip("networkx")
        from hledac.universal.recon._graph_serde import load_nx_graph_jsonl, save_nx_graph_jsonl

        g = nx.Graph()
        for i in range(100):
            g.add_node(f"n{i}")
        for i in range(99):
            g.add_edge(f"n{i}", f"n{i + 1}", weight=1.0)
        out = tmp_graphs_dir / "pruned.jsonl"
        ok = save_nx_graph_jsonl(str(out), g, max_nodes=20)
        assert ok is True
        loaded = load_nx_graph_jsonl(str(out), max_nodes=20)
        assert loaded is not None
        # Should be pruned to 20
        assert loaded.number_of_nodes() == 20

    def test_bound_preserves_edges(self, tmp_graphs_dir: Path) -> None:
        nx = pytest.importorskip("networkx")
        from hledac.universal.recon._graph_serde import load_nx_graph_jsonl, save_nx_graph_jsonl

        g = nx.Graph()
        for i in range(50):
            g.add_node(f"n{i}")
        for i in range(49):
            g.add_edge(f"n{i}", f"n{i + 1}")
        out = tmp_graphs_dir / "chain.jsonl"
        save_nx_graph_jsonl(str(out), g, max_nodes=10)
        loaded = load_nx_graph_jsonl(str(out), max_nodes=10)
        assert loaded is not None
        assert loaded.number_of_nodes() == 10
        # Edges between surviving nodes should be intact
        assert loaded.number_of_edges() > 0


# ---------------------------------------------------------------------------
# Test: legacy pickle migration (F196B safe path only)
# ---------------------------------------------------------------------------


class TestGraphSerdeLegacyMigration:
    def test_legacy_pickle_loads_on_safe_path(self, tmp_graphs_dir: Path) -> None:
        nx = pytest.importorskip("networkx")
        import pickle

        from hledac.universal.recon._graph_serde import load_nx_graph_jsonl

        legacy_g = nx.Graph()
        legacy_g.add_node("legacy_a")
        legacy_g.add_node("legacy_b")
        legacy_g.add_edge("legacy_a", "legacy_b", weight=0.7)
        # Write a real Python pickle
        legacy_path = tmp_graphs_dir / "legacy.pkl"
        with open(legacy_path, "wb") as f:
            pickle.dump(legacy_g, f, protocol=pickle.HIGHEST_PROTOCOL)

        # is_our_format must be False (legacy is not our JSON)
        from hledac.universal.recon._graph_serde import is_our_format

        assert is_our_format(str(legacy_path)) is False

        # load_nx_graph_jsonl should still load it (legacy migration path)
        loaded = load_nx_graph_jsonl(str(legacy_path), max_nodes=10_000)
        assert loaded is not None
        assert loaded.number_of_nodes() == 2
        assert loaded.number_of_edges() == 1

    def test_safe_path_rejects_outside_graphs_dir(self, tmp_path: Path, monkeypatch) -> None:
        nx = pytest.importorskip("networkx")
        import pickle

        from hledac.universal.recon._graph_serde import load_nx_graph_jsonl

        # Make a non-graphs-dir location
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        # HOME points to fake_home so _safe_path() would reject anything outside
        fake_home = tmp_path / "hledac_home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        legacy_g = nx.Graph()
        legacy_g.add_node("malicious")
        outside_file = outside / "malicious.pkl"
        with open(outside_file, "wb") as f:
            pickle.dump(legacy_g, f)

        # Should refuse (F196B)
        loaded = load_nx_graph_jsonl(str(outside_file), max_nodes=10_000)
        assert loaded is None


# ---------------------------------------------------------------------------
# Test: relationship_discovery integration — no Python pickle in hot path
# ---------------------------------------------------------------------------


class TestRelationshipDiscoveryNoPickleInHotPath:
    """Verify the 5 remaining ``pickle`` refs in relationship_discovery.py
    are all legacy-migration-only (F196B-safe)."""

    def test_remaining_pickle_sites_are_legacy_only(self) -> None:
        from pathlib import Path

        src_path = Path(__file__).parent.parent / "intelligence" / "relationship_discovery.py"
        text = src_path.read_text()
        # Every remaining ``import pickle`` must be immediately followed by
        # a legacy-migration comment OR a path-safety check.
        # We enforce: each "import pickle" line must be within 3 lines of
        # a comment containing "legacy" or "F196B".
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if "import pickle" in line and "from pickle" not in line:
                window = "\n".join(lines[max(0, i - 3) : i + 3])
                assert "legacy" in window.lower() or "f196b" in window.lower(), (
                    f"Line {i + 1} imports pickle without legacy/F196B guard: {line!r}"
                )

    def test_no_bare_pickle_load_in_writes(self) -> None:
        """``pickle.dump`` must never appear in our module (only pickle.load
        for legacy reads)."""
        from pathlib import Path

        src_path = Path(__file__).parent.parent / "intelligence" / "relationship_discovery.py"
        text = src_path.read_text()
        assert "pickle.dump" not in text, "pickle.dump must NOT appear — new writes are JSON+orjson only"


# ---------------------------------------------------------------------------
# Test: igraph write_picklez is the igraph native format, not Python pickle
# ---------------------------------------------------------------------------


class TestIgraphNativeFormatUnchanged:
    def test_igraph_write_picklez_preserved(self) -> None:
        """The igraph hot path keeps its own ``write_picklez`` — that is
        igraph's C-level compact format, NOT the Python ``pickle`` module."""
        from pathlib import Path

        src_path = Path(__file__).parent.parent / "intelligence" / "relationship_discovery.py"
        text = src_path.read_text()
        # The igraph hot path must still call write_picklez
        assert "write_picklez" in text
        # And it must be gated on IGRAPH_AVAILABLE
        assert "IGRAPH_AVAILABLE" in text


# ---------------------------------------------------------------------------
# Test: orjson speed vs pickle (informational, not gating)
# ---------------------------------------------------------------------------


class TestGraphSerdeSmoke:
    def test_module_imports_cleanly(self) -> None:
        import hledac.universal.recon._graph_serde as mod

        assert mod.save_nx_graph_jsonl is not None
        assert mod.load_nx_graph_jsonl is not None
        assert mod.is_our_format is not None

    def test_failure_is_soft(self, tmp_graphs_dir: Path, capsys) -> None:
        """save_nx_graph_jsonl with a non-Graph object returns False, no raise."""
        from hledac.universal.recon._graph_serde import save_nx_graph_jsonl

        # Pass None
        ok = save_nx_graph_jsonl(str(tmp_graphs_dir / "x.jsonl"), None, max_nodes=10)
        assert ok is False