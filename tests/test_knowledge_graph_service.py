"""
Test Sprint F195C Graph Service Integration
=========================================

Acceptance criteria:
- graph upsert is idempotent
- history lookup can return known entity
- sprint continues on graph failure
- Final: pytest tests/test_knowledge_graph_service.py -q
"""

from unittest.mock import MagicMock, patch


class TestGraphServiceUpsertIdempotent:
    """graph upsert is idempotent"""

    def test_upsert_ioc_twice_same_value(self):
        """Second upsert returns False (already seen) — graph.add_ioc called only once."""
        from hledac.universal.knowledge import graph_service as gs

        # Reset to clean state
        gs._SEEN_IOCS.clear()
        gs._SEEN_RELS.clear()

        mock_graph = MagicMock()
        mock_graph.add_ioc = MagicMock(return_value=12345)

        with patch.object(gs, '_get_graph', return_value=mock_graph):
            r1 = gs.upsert_ioc("1.2.3.4", "ip", 0.9, "test")
            r2 = gs.upsert_ioc("1.2.3.4", "ip", 0.9, "test")

            assert r1 is True
            assert r2 is False  # idempotent — second call skips without graph call
            assert mock_graph.add_ioc.call_count == 1  # graph called only once

    def test_upsert_relation_twice_same_edge(self):
        """Second upsert_relation call is idempotent — returns False without graph call."""
        from hledac.universal.knowledge import graph_service as gs

        # Reset to clean state
        gs._SEEN_RELS.clear()
        gs._SEEN_IOCS.clear()

        mock_graph = MagicMock()

        with patch.object(gs, '_get_graph', return_value=mock_graph):
            r1 = gs.upsert_relation("a.com", "b.com", "links_to", 0.8)
            r2 = gs.upsert_relation("a.com", "b.com", "links_to", 0.8)

            assert r1 is True
            assert r2 is False  # idempotent — second call skips
            # Second call should NOT contact the graph (idempotency check before graph call)
            assert mock_graph.add_relation.call_count == 1


class TestGraphServiceHistoryLookup:
    """history lookup can return known entity"""

    def test_find_entity_history_returns_known(self):
        """find_entity_history returns connected entities from DuckPGQGraph."""
        from hledac.universal.knowledge import graph_service

        expected = [
            {"value": "evil.com", "ioc_type": "domain", "confidence": 0.9, "source": "test"}
        ]
        mock_graph = MagicMock()
        mock_graph.find_connected = MagicMock(return_value=expected)

        with patch.object(graph_service, '_get_graph', return_value=mock_graph):
            result = graph_service.find_entity_history("1.2.3.4", max_hops=2)

            assert result == expected
            mock_graph.find_connected.assert_called_once_with("1.2.3.4", 2)

    def test_find_entity_history_empty_when_no_connections(self):
        """find_entity_history returns [] when entity has no connections."""
        from hledac.universal.knowledge import graph_service

        mock_graph = MagicMock()
        mock_graph.find_connected = MagicMock(return_value=[])

        with patch.object(graph_service, '_get_graph', return_value=mock_graph):
            result = graph_service.find_entity_history("9.9.9.9")

            assert result == []

    def test_find_entity_history_defaults_to_2_hops(self):
        """find_entity_history defaults to max_hops=2 when not specified."""
        from hledac.universal.knowledge import graph_service

        mock_graph = MagicMock()
        mock_graph.find_connected = MagicMock(return_value=[])

        with patch.object(graph_service, '_get_graph', return_value=mock_graph):
            graph_service.find_entity_history("1.1.1.1")

            mock_graph.find_connected.assert_called_once_with("1.1.1.1", 2)


class TestGraphServiceFailSafe:
    """sprint continues on graph failure"""

    def test_upsert_ioc_returns_false_when_graph_none(self):
        """upsert_ioc returns False (not exception) when graph unavailable."""
        from hledac.universal.knowledge import graph_service

        with patch.object(graph_service, '_get_graph', return_value=None):
            result = graph_service.upsert_ioc("1.2.3.4", "ip")

            assert result is False

    def test_upsert_relation_returns_false_when_graph_none(self):
        """upsert_relation returns False (not exception) when graph unavailable."""
        from hledac.universal.knowledge import graph_service

        with patch.object(graph_service, '_get_graph', return_value=None):
            result = graph_service.upsert_relation("a.com", "b.com", "links_to")

            assert result is False

    def test_find_entity_history_returns_empty_when_graph_none(self):
        """find_entity_history returns [] (not exception) when graph unavailable."""
        from hledac.universal.knowledge import graph_service

        with patch.object(graph_service, '_get_graph', return_value=None):
            result = graph_service.find_entity_history("1.2.3.4")

            assert result == []

    def test_graph_stats_returns_empty_dict_when_graph_none(self):
        """graph_stats returns {} (not exception) when graph unavailable."""
        from hledac.universal.knowledge import graph_service

        with patch.object(graph_service, '_get_graph', return_value=None):
            result = graph_service.graph_stats()

            assert result == {}

    def test_checkpoint_noop_when_graph_none(self):
        """checkpoint is a no-op (not exception) when graph unavailable."""
        from hledac.universal.knowledge import graph_service

        with patch.object(graph_service, '_get_graph', return_value=None):
            # Should not raise
            graph_service.checkpoint()

    def test_upsert_ioc_swallows_exception(self):
        """upsert_ioc returns False when graph.add_ioc raises."""
        from hledac.universal.knowledge import graph_service

        mock_graph = MagicMock()
        mock_graph.add_ioc = MagicMock(side_effect=RuntimeError("DB error"))

        with patch.object(graph_service, '_get_graph', return_value=mock_graph):
            result = graph_service.upsert_ioc("1.2.3.4", "ip")

            assert result is False

    def test_find_entity_history_swallows_exception(self):
        """find_entity_history returns [] when graph.find_connected raises."""
        from hledac.universal.knowledge import graph_service

        mock_graph = MagicMock()
        mock_graph.find_connected = MagicMock(side_effect=RuntimeError("query error"))

        with patch.object(graph_service, '_get_graph', return_value=mock_graph):
            result = graph_service.find_entity_history("1.2.3.4")

            assert result == []
