"""
Sprint F206G: Graph Analytics Activation Tests
===============================================

Tests verify bounded graph analytics signal is activated for analyst brief
and sprint report WITHOUT creating a new graph authority.

Invariant mapping:
  F206G-1 | graph_analytics_summary() returns dict with required keys
  F206G-2 | graph_analytics_summary() returns empty when graph unavailable
  F206G-3 | graph_analytics_summary() fail-soft on exception
  F206G-4 | graph_analytics_summary() top_k bounded to MAX_GRAPH_ANALYTICS_TOP_K (10)
  F206G-5 | build_sprint_brief includes at most 2 graph analytics findings
  F206G-6 | build_sprint_brief excludes graph findings when analytics unavailable
  F206G-7 | build_sprint_brief is fail-soft on graph analytics error
  F206G-8 | No persistent writes in graph_analytics_summary()
  F206G-9 | MAX_GRAPH_ANALYTICS_NODES = 500 bound respected
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from dataclasses import dataclass

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ============================================================================
# Test: graph_analytics_summary module-level function
# ============================================================================


class TestGraphAnalyticsSummary:
    """Tests for the module-level graph_analytics_summary() function."""

    def test_invariant_ga1_returns_correct_structure(self):
        """F206G-1: Returns dict with all required keys when graph available."""
        from hledac.universal.knowledge import graph_service

        mock_graph = MagicMock()
        mock_graph.get_top_nodes_by_degree.return_value = [
            {"value": "evil.com", "ioc_type": "domain", "degree": 15},
            {"value": "192.168.1.1", "ioc_type": "ipv4", "degree": 8},
        ]
        mock_graph.con = MagicMock()
        mock_graph.con.execute.return_value.fetchall.return_value = [(10,)]

        with patch.object(graph_service, "_get_graph", return_value=mock_graph):
            result = graph_service.graph_analytics_summary(top_k=10)

        assert isinstance(result, dict)
        assert "top_central_entities" in result
        assert "community_count" in result
        assert "analytics_available" in result
        assert "skipped_reason" in result
        assert result["analytics_available"] is True
        assert len(result["top_central_entities"]) == 2
        assert result["top_central_entities"][0]["value"] == "evil.com"
        assert result["top_central_entities"][0]["degree"] == 15

    def test_invariant_ga2_returns_empty_when_graph_unavailable(self):
        """F206G-2: Returns empty result when graph is None."""
        from hledac.universal.knowledge import graph_service

        with patch.object(graph_service, "_get_graph", return_value=None):
            result = graph_service.graph_analytics_summary()

        assert result["analytics_available"] is False
        assert result["skipped_reason"] == "graph_unavailable"
        assert result["top_central_entities"] == []
        assert result["community_count"] == 0

    def test_invariant_ga3_fail_soft_on_exception(self):
        """F206G-3: Exception from graph methods is caught — never propagates."""
        from hledac.universal.knowledge import graph_service

        mock_graph = MagicMock()
        mock_graph.get_top_nodes_by_degree.side_effect = OSError("duckdb corrupted")
        mock_graph.con = MagicMock()

        with patch.object(graph_service, "_get_graph", return_value=mock_graph):
            result = graph_service.graph_analytics_summary()

        assert result["analytics_available"] is False
        assert "duckdb corrupted" in result["skipped_reason"]
        assert result["top_central_entities"] == []

    def test_invariant_ga4_top_k_bounded(self):
        """F206G-4: top_k is capped to MAX_GRAPH_ANALYTICS_TOP_K=10."""
        from hledac.universal.knowledge import graph_service

        mock_graph = MagicMock()
        mock_graph.get_top_nodes_by_degree.return_value = []
        mock_graph.con = MagicMock()
        mock_graph.con.execute.return_value.fetchall.return_value = [(0,)]

        with patch.object(graph_service, "_get_graph", return_value=mock_graph):
            result = graph_service.graph_analytics_summary(top_k=100)  # Over limit

        # get_top_nodes_by_degree should have been called with min(100, 10) = 10
        mock_graph.get_top_nodes_by_degree.assert_called_once_with(
            n=graph_service.MAX_GRAPH_ANALYTICS_TOP_K
        )
        assert result["analytics_available"] is True

    def test_invariant_ga9_max_analytics_nodes_respected(self):
        """F206G-9: MAX_GRAPH_ANALYTICS_NODES=500 bound respected in edge query."""
        from hledac.universal.knowledge import graph_service

        mock_graph = MagicMock()
        mock_graph.get_top_nodes_by_degree.return_value = []
        mock_graph.con = MagicMock()
        mock_graph.con.execute.return_value.fetchall.return_value = [(500,)]

        with patch.object(graph_service, "_get_graph", return_value=mock_graph):
            graph_service.graph_analytics_summary()

        # Verify the LIMIT clause used MAX_GRAPH_ANALYTICS_NODES
        call_args = mock_graph.con.execute.call_args
        sql_query = str(call_args)
        assert f"LIMIT {graph_service.MAX_GRAPH_ANALYTICS_NODES}" in sql_query

    def test_invariant_ga8_no_persistent_writes(self):
        """F206G-8: graph_analytics_summary does no writes (checkpoint not called)."""
        from hledac.universal.knowledge import graph_service

        mock_graph = MagicMock()
        mock_graph.get_top_nodes_by_degree.return_value = []
        mock_graph.con = MagicMock()
        mock_graph.con.execute.return_value.fetchall.return_value = [(0,)]

        with patch.object(graph_service, "_get_graph", return_value=mock_graph):
            graph_service.graph_analytics_summary()

        # checkpoint() should NOT be called
        mock_graph.checkpoint.assert_not_called()
        # con.execute should only be called for reads (SELECT), not INSERT/UPDATE/DELETE
        for call in mock_graph.con.execute.call_args_list:
            sql = str(call).upper()
            assert "INSERT" not in sql
            assert "UPDATE" not in sql
            assert "DELETE" not in sql


# ============================================================================
# Test: build_sprint_brief uses graph analytics
# ============================================================================


class TestBriefGraphAnalyticsIntegration:
    """Tests for graph analytics integration in build_sprint_brief."""

    @pytest.fixture
    def mock_workbench(self):
        """Minimal workbench with graph_service mocked."""
        from hledac.universal.knowledge.analyst_workbench import AnalystWorkbench

        wb = AnalystWorkbench(
            duckdb_store=MagicMock(),
            graph_service=MagicMock(),
            vector_store=MagicMock(),
            semantic_store=MagicMock(),
        )
        return wb

    @pytest.mark.asyncio
    async def test_invariant_ba1_brief_includes_up_to_2_graph_findings(self, mock_workbench):
        """F206G-5: Brief includes at most 2 graph analytics findings when available."""
        from hledac.universal.knowledge import analyst_workbench as aw

        mock_graph_result = {
            "top_central_entities": [
                {"value": "evil.com", "ioc_type": "domain", "degree": 15},
                {"value": "C2.net", "ioc_type": "domain", "degree": 7},
                {"value": "old.biz", "ioc_type": "domain", "degree": 3},
            ],
            "community_count": 3,
            "analytics_available": True,
            "skipped_reason": None,
        }

        with patch(
            "hledac.universal.knowledge.graph_service.graph_analytics_summary",
            return_value=mock_graph_result,
        ):
            brief = await mock_workbench.build_sprint_brief(
                sprint_id="s-1",
                target_id="test-target",
                findings=[],
                graph_signal={},
                governor=None,
                duckdb_store=None,
            )

        # Should have at most 2 graph findings
        graph_findings = [
            f for f in brief.key_findings
            if "Graph central entity" in f or "Graph entity 2" in f or "Graph communities" in f
        ]
        assert len(graph_findings) <= 2

        # First finding should be the top entity
        assert any("evil.com" in f for f in graph_findings)
        assert any("degree=15" in f for f in graph_findings)

    @pytest.mark.asyncio
    async def test_invariant_ba2_brief_excludes_graph_when_unavailable(self, mock_workbench):
        """F206G-6: Brief excludes graph findings when analytics unavailable."""
        from hledac.universal.knowledge import analyst_workbench as aw

        mock_graph_result = {
            "top_central_entities": [],
            "community_count": 0,
            "analytics_available": False,
            "skipped_reason": "graph_unavailable",
        }

        with patch(
            "hledac.universal.knowledge.graph_service.graph_analytics_summary",
            return_value=mock_graph_result,
        ):
            brief = await mock_workbench.build_sprint_brief(
                sprint_id="s-1",
                target_id="test-target",
                findings=[],
                graph_signal={},
                governor=None,
                duckdb_store=None,
            )

        graph_findings = [
            f for f in brief.key_findings
            if "Graph central entity" in f or "Graph entity 2" in f or "Graph communities" in f
        ]
        assert len(graph_findings) == 0

    @pytest.mark.asyncio
    async def test_invariant_ba3_brief_fail_soft_on_analytics_error(self, mock_workbench):
        """F206G-7: Brief is fail-soft when graph_analytics_summary raises."""
        from hledac.universal.knowledge import analyst_workbench as aw

        with patch(
            "hledac.universal.knowledge.graph_service.graph_analytics_summary",
            side_effect=OSError("import failed"),
        ):
            # Should NOT raise — fail-soft
            brief = await mock_workbench.build_sprint_brief(
                sprint_id="s-1",
                target_id="test-target",
                findings=[],
                graph_signal={},
                governor=None,
                duckdb_store=None,
            )

        # Brief should still be generated (fallback)
        assert brief.sprint_id == "s-1"
        assert brief.headline is not None

    @pytest.mark.asyncio
    async def test_community_count_as_second_finding_when_single_entity(self, mock_workbench):
        """When only 1 top entity, community_count is used as second finding."""
        from hledac.universal.knowledge import analyst_workbench as aw

        mock_graph_result = {
            "top_central_entities": [
                {"value": "solo.com", "ioc_type": "domain", "degree": 5},
            ],
            "community_count": 4,
            "analytics_available": True,
            "skipped_reason": None,
        }

        with patch(
            "hledac.universal.knowledge.graph_service.graph_analytics_summary",
            return_value=mock_graph_result,
        ):
            brief = await mock_workbench.build_sprint_brief(
                sprint_id="s-1",
                target_id="test-target",
                findings=[],
                graph_signal={},
                governor=None,
                duckdb_store=None,
            )

        graph_findings = [
            f for f in brief.key_findings
            if "Graph central entity" in f or "Graph entity 2" in f or "Graph communities" in f
        ]
        assert len(graph_findings) == 2
        assert any("solo.com" in f for f in graph_findings)
        assert any("4 detected communities" in f for f in graph_findings)


# ============================================================================
# Test: graph_analytics_summary BOUNDS
# ============================================================================


class TestGraphAnalyticsBounds:
    """Verify all BOUNDS constants are correctly set."""

    def test_max_analytics_nodes_bound(self):
        from hledac.universal.knowledge import graph_service
        assert graph_service.MAX_GRAPH_ANALYTICS_NODES == 500

    def test_max_analytics_top_k_bound(self):
        from hledac.universal.knowledge import graph_service
        assert graph_service.MAX_GRAPH_ANALYTICS_TOP_K == 10

    def test_max_brief_graph_findings_bound(self):
        from hledac.universal.knowledge import analyst_workbench as aw
        assert aw.MAX_GRAPH_ANALYTICS_BRIEF_FINDINGS == 2
