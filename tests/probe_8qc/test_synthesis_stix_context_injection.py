"""
Sprint 8QC D.10: STIX context injection from ioc_graph.
100% offline — mocks.
"""

from unittest.mock import MagicMock

from hledac.universal.brain.synthesis_runner import SynthesisRunner


class MockIOCGraph:
    """Mock IOCGraph with export_stix_bundle."""
    def __init__(self, nodes):
        self._nodes = nodes

    def export_stix_bundle(self):
        return self._nodes


class TestSTIXContext:
    """D.10: STIX context from ioc_graph.export_stix_bundle() injected into prompt."""

    def test_stix_context_injected_when_graph_available(self):
        """_build_stix_context returns IOC values when graph is injected."""
        mock_graph = MockIOCGraph([
            {"value": "1.2.3.4", "ioc_type": "ip"},
            {"value": "evil.com", "ioc_type": "domain"},
            {"value": "CVE-2026-9999", "ioc_type": "cve"},
        ])
        runner = SynthesisRunner(MagicMock())  # type: ignore[arg-type]
        runner._ioc_graph = mock_graph

        context = runner._build_stix_context()

        assert "Known IOCs from graph" in context
        assert "1.2.3.4" in context
        assert "evil.com" in context
        assert "CVE-2026-9999" in context

    def test_stix_context_empty_when_no_graph(self):
        """_build_stix_context returns empty string when no graph injected."""
        runner = SynthesisRunner(MagicMock())  # type: ignore[arg-type]
        runner._ioc_graph = None

        context = runner._build_stix_context()

        assert context == ""

    def test_stix_context_empty_when_export_returns_nothing(self):
        """_build_stix_context returns empty when export_stix_bundle returns empty."""
        mock_graph = MagicMock()
        mock_graph.export_stix_bundle.return_value = []

        runner = SynthesisRunner(MagicMock())  # type: ignore[arg-type]
        runner._ioc_graph = mock_graph

        context = runner._build_stix_context()

        assert context == ""
