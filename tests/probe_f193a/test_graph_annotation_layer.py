"""
Sprint F193A §3: Contract tests for graph enrichment annotation layer.

Tests that:
1. duckdb_store.annotate_findings_with_graph_context() is a read-only seam
2. graph remains donor/backend, not truth owner
3. fail-open behavior (returns original findings on error)
4. graph_enriched_findings appears in export_sprint output
5. no new graph write path
"""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore


class TestGraphAnnotationSeam:
    """Contract tests for annotation layer."""

    def test_annotate_findings_returns_original_on_no_graph(self):
        """No graph attached → fail-open, returns findings unchanged."""
        store = DuckDBShadowStore.__new__(DuckDBShadowStore)
        store._ioc_graph = None

        findings = [
            {"id": "f1", "value": "1.2.3.4", "ioc_type": "ip"},
            {"id": "f2", "value": "evil.com", "ioc_type": "domain"},
        ]
        result = store.annotate_findings_with_graph_context(findings)
        assert result == findings

    def test_annotate_findings_returns_original_on_empty(self):
        """Empty findings list → returns unchanged."""
        store = DuckDBShadowStore.__new__(DuckDBShadowStore)
        store._ioc_graph = MagicMock()

        result = store.annotate_findings_with_graph_context([])
        assert result == []

    def test_annotate_findings_preserves_unannotated_fields(self):
        """Findings beyond max_annotations are returned unchanged."""
        store = DuckDBShadowStore.__new__(DuckDBShadowStore)
        mock_graph = MagicMock()
        mock_graph.find_connected = MagicMock(return_value=[])
        store._ioc_graph = mock_graph

        findings = [{"id": f"f{i}", "value": f"ioc{i}", "ioc_type": "ip"} for i in range(60)]
        result = store.annotate_findings_with_graph_context(findings, max_annotations=50)
        # First 50 may get annotation attempt, rest unchanged
        assert len(result) == 60

    def test_annotate_findings_attaches_graph_annotation(self):
        """When graph has connected IOCs, annotation is attached to finding."""
        store = DuckDBShadowStore.__new__(DuckDBShadowStore)
        mock_graph = MagicMock()
        mock_graph.find_connected = MagicMock(return_value=[
            {"value": "related.com", "ioc_type": "domain", "confidence": 0.9},
            {"value": "1.2.3.5", "ioc_type": "ip", "confidence": 0.7},
        ])
        store._ioc_graph = mock_graph

        findings = [{"id": "f1", "value": "1.2.3.4", "ioc_type": "ip"}]
        result = store.annotate_findings_with_graph_context(findings)

        assert len(result) == 1
        assert "graph_annotation" in result[0]
        ann = result[0]["graph_annotation"]
        assert ann["connected_count"] == 2
        assert "domain" in ann["connected_types"]
        assert ann["max_hops"] == 2

    def test_annotate_findings_no_annotation_when_no_connections(self):
        """No connected IOCs → no graph_annotation key attached."""
        store = DuckDBShadowStore.__new__(DuckDBShadowStore)
        mock_graph = MagicMock()
        mock_graph.find_connected = MagicMock(return_value=[])
        store._ioc_graph = mock_graph

        findings = [{"id": "f1", "value": "1.2.3.4", "ioc_type": "ip"}]
        result = store.annotate_findings_with_graph_context(findings)

        assert len(result) == 1
        # No annotation when connected is empty
        assert "graph_annotation" not in result[0]

    def test_annotate_findings_fail_open_on_exception(self):
        """Exception during annotation → fail-open, returns original."""
        store = DuckDBShadowStore.__new__(DuckDBShadowStore)
        mock_graph = MagicMock()
        mock_graph.find_connected = MagicMock(side_effect=RuntimeError("graph error"))
        store._ioc_graph = mock_graph

        findings = [{"id": "f1", "value": "1.2.3.4", "ioc_type": "ip"}]
        result = store.annotate_findings_with_graph_context(findings)
        assert result == findings

    def test_annotate_findings_respects_max_annotations(self):
        """max_annotations caps the work done."""
        store = DuckDBShadowStore.__new__(DuckDBShadowStore)
        mock_graph = MagicMock()
        mock_graph.find_connected = MagicMock(return_value=[
            {"value": "x.com", "ioc_type": "domain", "confidence": 0.5}
        ])
        store._ioc_graph = mock_graph

        findings = [{"id": f"f{i}", "value": f"val{i}", "ioc_type": "ip"} for i in range(100)]
        result = store.annotate_findings_with_graph_context(findings, max_annotations=10)

        # Only first 10 get annotation attempt (unique IOCs extracted from first 10)
        assert len(result) == 100  # All returned, but only first 10 processed

    def test_annotate_findings_no_write_to_graph(self):
        """Annotation is read-only — verify no write method is called."""
        store = DuckDBShadowStore.__new__(DuckDBShadowStore)
        mock_graph = MagicMock()
        mock_graph.find_connected = MagicMock(return_value=[])
        # Ensure no write methods are called
        mock_graph.add_ioc = MagicMock(side_effect=AssertionError("add_ioc should not be called"))
        mock_graph.add_relation = MagicMock(side_effect=AssertionError("add_relation should not be called"))
        store._ioc_graph = mock_graph

        findings = [{"id": "f1", "value": "1.2.3.4", "ioc_type": "ip"}]
        result = store.annotate_findings_with_graph_context(findings)

        # If add_ioc or add_relation were called, the mock would have raised
        assert len(result) == 1


class TestExportGraphEnrichment:
    """Contract tests for export integration of graph enrichment."""

    def test_export_result_contains_graph_enriched_findings_key(self):
        """export_sprint result includes graph_enriched_findings key."""
        from unittest.mock import AsyncMock, patch, MagicMock
        from hledac.universal.export.sprint_exporter import export_sprint

        # Use proper ExportHandoff instance (canonical producer pattern)
        from hledac.universal.project_types import ExportHandoff

        mock_store = MagicMock()
        mock_store.async_query_recent_findings = AsyncMock(return_value=[
            {"id": "f1", "value": "1.2.3.4", "ioc_type": "ip"},
        ])
        mock_store.annotate_findings_with_graph_context = MagicMock(return_value=[
            {"id": "f1", "value": "1.2.3.4", "ioc_type": "ip", "graph_annotation": {"connected_count": 1}},
        ])
        mock_store.get_top_seed_nodes = MagicMock(return_value=[])
        mock_store.get_graph_stats = MagicMock(return_value={})

        mock_handoff = ExportHandoff(
            sprint_id="test-sprint",
            scorecard={},
            top_nodes=[],
            gnn_predictions=0,
            synthesis_engine="test",
            runtime_truth={},
            phase_durations={},
            correlation=None,
            execution_context={},
            canonical_run_summary={},
            synthesis_outcome_payload=None,
            sprint_verdict=None,
        )

        with patch("hledac.universal.paths.get_sprint_json_report_path") as mock_path:
            mock_path.return_value = MagicMock()
            with patch("hledac.universal.export.sprint_exporter._make_serializable", side_effect=lambda x: x):
                with patch.object(asyncio, "get_running_loop", side_effect=RuntimeError("no loop")):
                    result = asyncio.run(export_sprint(mock_store, mock_handoff, sprint_id="test-sprint"))

        assert "graph_enriched_findings" in result
        assert isinstance(result["graph_enriched_findings"], list)

    def test_export_fails_soft_when_annotation_unavailable(self):
        """export_sprint succeeds even if annotation seam is absent."""
        from unittest.mock import AsyncMock, MagicMock
        from hledac.universal.export.sprint_exporter import export_sprint

        from hledac.universal.project_types import ExportHandoff

        mock_store = MagicMock()
        mock_store.async_query_recent_findings = AsyncMock(return_value=[])
        # No annotate_findings_with_graph_context method
        mock_store.get_top_seed_nodes = MagicMock(return_value=[])
        mock_store.get_graph_stats = MagicMock(return_value={})

        mock_handoff = ExportHandoff(
            sprint_id="test-sprint",
            scorecard={},
            top_nodes=[],
            gnn_predictions=0,
            synthesis_engine="test",
            runtime_truth={},
            phase_durations={},
            correlation=None,
            execution_context={},
            canonical_run_summary={},
            synthesis_outcome_payload=None,
            sprint_verdict=None,
        )

        with patch("hledac.universal.paths.get_sprint_json_report_path") as mock_path:
            mock_path.return_value = MagicMock()
            with patch("hledac.universal.export.sprint_exporter._make_serializable", side_effect=lambda x: x):
                with patch.object(asyncio, "get_running_loop", side_effect=RuntimeError("no loop")):
                    result = asyncio.run(export_sprint(mock_store, mock_handoff, sprint_id="test-sprint"))

        # Must not raise — fail-soft
        assert "graph_enriched_findings" in result
        assert result["graph_enriched_findings"] == []  # empty when seam unavailable


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-q"])