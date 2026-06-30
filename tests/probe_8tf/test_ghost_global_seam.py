"""
Sprint 8TF §2: ghost_global store seam tests.
Sprint: 8TF
Area: ghost_global entity export — bounded store seam

Tests:
1. get_top_entities_for_ghost_global() returns correct tuple shape
2. Fail-soft when _ioc_graph is None
3. Fail-soft when graph has no get_top_nodes_by_degree
4. __main__.py no longer does direct graph spelunking for ghost_global
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestGhostGlobalSeam:
    """duckdb_store.get_top_entities_for_ghost_global() — shape and fail-soft."""

    def test_returns_list_of_tuples(self):
        """Returns list[tuple[str, str, float]] matching upsert_global_entities sig."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        mock_graph = MagicMock()
        mock_graph.get_top_nodes_by_degree.return_value = [
            {"value": "evil.com", "ioc_type": "domain", "confidence": 0.95},
            {"value": "1.2.3.4", "ioc_type": "ipv4", "confidence": 0.88},
        ]
        store._ioc_graph = mock_graph

        result = store.get_top_entities_for_ghost_global(n=100)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == ("evil.com", "domain", 0.95)
        assert result[1] == ("1.2.3.4", "ipv4", 0.88)

    def test_failsoft_no_graph(self):
        """Returns [] when _ioc_graph is None."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        store._ioc_graph = None

        result = store.get_top_entities_for_ghost_global(n=100)

        assert result == []

    def test_failsoft_no_method(self):
        """Returns [] when graph has no get_top_nodes_by_degree."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        store._ioc_graph = MagicMock(spec=["other_method"])  # no get_top_nodes_by_degree

        result = store.get_top_entities_for_ghost_global(n=100)

        assert result == []

    def test_failsoft_method_raises(self):
        """Returns [] when get_top_nodes_by_degree raises."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        mock_graph = MagicMock()
        mock_graph.get_top_nodes_by_degree.side_effect = RuntimeError("DB not ready")
        store._ioc_graph = mock_graph

        result = store.get_top_entities_for_ghost_global(n=100)

        assert result == []

    def test_empty_value_filtered(self):
        """Nodes with empty value are filtered out."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        mock_graph = MagicMock()
        mock_graph.get_top_nodes_by_degree.return_value = [
            {"value": "", "ioc_type": "domain", "confidence": 0.5},
            {"value": "valid.io", "ioc_type": "domain", "confidence": 0.8},
        ]
        store._ioc_graph = mock_graph

        result = store.get_top_entities_for_ghost_global(n=100)

        assert len(result) == 1
        assert result[0] == ("valid.io", "domain", 0.8)

    def test_n_parameter_passed_to_graph(self):
        """The n parameter is passed to get_top_nodes_by_degree."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        mock_graph = MagicMock()
        mock_graph.get_top_nodes_by_degree.return_value = []
        store._ioc_graph = mock_graph

        store.get_top_entities_for_ghost_global(n=50)

        mock_graph.get_top_nodes_by_degree.assert_called_once_with(n=50)


class TestGhostGlobalMainNoSpelunking:
    """__main__.py no longer does direct graph spelunking for ghost_global use case."""

    def test_main_uses_store_seam(self):
        """__main__.py ghost_global block calls store seam, not graph.get_nodes()."""
        main_path = Path(__file__).parent.parent.parent / "__main__.py"
        with open(main_path) as f:
            source = f.read()

        lines = source.split("\n")
        # Find the section start (first line with "8TF" + ghost_global)
        start_idx = None
        for i, line in enumerate(lines):
            if "ghost_global" in line and "8TF" in line:
                start_idx = i
                break

        assert start_idx is not None, "Could not find ghost_global 8TF section"

        # Collect all lines until we see the seam call
        ghost_lines = []
        for line in lines[start_idx:]:
            ghost_lines.append(line)
            if "get_top_entities_for_ghost_global" in line:
                break

        ghost_src = "\n".join(ghost_lines)
        assert "get_top_entities_for_ghost_global" in ghost_src, \
            "__main__.py does not call get_top_entities_for_ghost_global"
        # graph.get_nodes() must not appear as actual code (only in comments)
        code_lines = [l for l in ghost_src.split("\n") if l.strip() and not l.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "get_nodes()" not in code_only, \
            "__main__.py still has direct graph.get_nodes() call in code"
