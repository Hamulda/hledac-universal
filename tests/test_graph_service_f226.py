"""
Test Sprint F226 — GraphService Instance Isolation
===================================================

Acceptance criteria:
- Two GraphService() instances have isolated _seen_iocs / _seen_rels
- Duplicate upsert in one instance is deduplicated
- Duplicate upsert in another instance is NOT blocked by the first
- Module-level facade works as before (backward compat)
- reset_session() clears the default facade instance state
- fail-soft semantics preserved

Run: pytest tests/test_graph_service_f226.py -q
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from hledac.universal.knowledge import graph_service
from hledac.universal.knowledge.graph_service import (
    GraphService,
    _DEFAULT_GRAPH_SERVICE,
    _SEEN_IOCS,
    _SEEN_RELS,
    upsert_ioc,
    upsert_relation,
    upsert_identity_edge,
    find_entity_history,
    graph_stats,
    checkpoint,
    reset_session,
    upsert_ioc_batch,
    graph_analytics_summary,
    _get_graph,
)


class TestGraphServiceInstanceIsolation:
    """Two GraphService instances must have isolated state."""

    def test_two_instances_have_separate_seen_iocs(self):
        """Fresh instances start with empty, independent _seen_iocs."""
        gs1_fresh = GraphService()
        gs2_fresh = GraphService()
        assert ("evil.com", "domain") not in gs1_fresh._seen_iocs
        assert ("good.com", "domain") not in gs2_fresh._seen_iocs
        assert gs1_fresh._seen_iocs is not gs2_fresh._seen_iocs

    def test_two_instances_have_separate_seen_rels(self):
        """Fresh instances start with empty, independent _seen_rels."""
        gs1_fresh = GraphService()
        gs2_fresh = GraphService()
        assert ("a.com", "b.com", "links_to") not in gs1_fresh._seen_rels
        assert ("c.com", "d.com", "links_to") not in gs2_fresh._seen_rels
        assert gs1_fresh._seen_rels is not gs2_fresh._seen_rels

    def test_duplicate_upsert_in_same_instance_blocked(self):
        """Duplicate upsert in same instance is idempotent (blocked by _seen_iocs)."""
        gs = GraphService()

        with patch.object(graph_service, '_get_graph', return_value=MagicMock()):
            r1 = gs.upsert_ioc("1.2.3.4", "ip", 0.9, "test")
            r2 = gs.upsert_ioc("1.2.3.4", "ip", 0.9, "test")
            assert r1 is True
            assert r2 is False  # already in _seen_iocs

    def test_duplicate_upsert_cross_instance_not_blocked(self):
        """Upsert blocked in instance A does NOT block upsert in instance B."""
        gs1 = GraphService()
        gs2 = GraphService()

        with patch.object(graph_service, '_get_graph', return_value=MagicMock()):
            r1 = gs1.upsert_ioc("1.2.3.4", "ip", 0.9, "test")
            assert r1 is True

            # gs2 has its own _seen_iocs — should also succeed
            r2 = gs2.upsert_ioc("1.2.3.4", "ip", 0.9, "test")
            assert r2 is True


class TestGraphServiceFacadeBackwardCompat:
    """Module-level functions delegate to _DEFAULT_GRAPH_SERVICE."""

    def test_module_upsert_ioc_delegates_to_default_instance(self):
        """Module-level upsert_ioc works against _DEFAULT_GRAPH_SERVICE."""
        _DEFAULT_GRAPH_SERVICE._seen_iocs.clear()

        with patch.object(graph_service, '_get_graph', return_value=MagicMock()):
            r1 = upsert_ioc("1.2.3.4", "ip", 0.9, "test")
            assert r1 is True
            r2 = upsert_ioc("1.2.3.4", "ip", 0.9, "test")
            assert r2 is False  # idempotent in same facade

    def test_module_upsert_relation_delegates_to_default_instance(self):
        """Module-level upsert_relation works against _DEFAULT_GRAPH_SERVICE."""
        _DEFAULT_GRAPH_SERVICE._seen_rels.clear()

        with patch.object(graph_service, '_get_graph', return_value=MagicMock()):
            r1 = upsert_relation("a.com", "b.com", "links_to", 0.8)
            assert r1 is True
            r2 = upsert_relation("a.com", "b.com", "links_to", 0.8)
            assert r2 is False

    def test_module_reset_session_clears_default_facade(self):
        """reset_session() clears _DEFAULT_GRAPH_SERVICE state."""
        _DEFAULT_GRAPH_SERVICE._seen_iocs.add(("test.com", "domain"))
        _DEFAULT_GRAPH_SERVICE._seen_rels.add(("a.com", "b.com", "links_to"))

        reset_session()

        assert len(_DEFAULT_GRAPH_SERVICE._seen_iocs) == 0
        assert len(_DEFAULT_GRAPH_SERVICE._seen_rels) == 0

    def test_module_seen_iocs_clear_works(self):
        """_SEEN_IOCS.clear() clears the default instance's _seen_iocs."""
        _DEFAULT_GRAPH_SERVICE._seen_iocs.add(("x.com", "domain"))
        assert ("x.com", "domain") in _DEFAULT_GRAPH_SERVICE._seen_iocs

        _SEEN_IOCS.clear()

        assert ("x.com", "domain") not in _DEFAULT_GRAPH_SERVICE._seen_iocs

    def test_module_seen_rels_clear_works(self):
        """_SEEN_RELS.clear() clears the default instance's _seen_rels."""
        _DEFAULT_GRAPH_SERVICE._seen_rels.add(("a.com", "b.com", "links_to"))
        assert ("a.com", "b.com", "links_to") in _DEFAULT_GRAPH_SERVICE._seen_rels

        _SEEN_RELS.clear()

        assert ("a.com", "b.com", "links_to") not in _DEFAULT_GRAPH_SERVICE._seen_rels

    def test_module_upsert_identity_edge_works(self):
        """upsert_identity_edge module function delegates correctly."""
        _DEFAULT_GRAPH_SERVICE._seen_rels.clear()

        with patch.object(graph_service, '_get_graph', return_value=MagicMock()):
            r = upsert_identity_edge("profile:A", "profile:B", 0.7, "same person")
            assert r is True


class TestGraphServiceFailSafe:
    """fail-soft semantics preserved for instance methods."""

    def test_upsert_ioc_returns_false_when_graph_none(self):
        """upsert_ioc returns False when graph unavailable."""
        gs = GraphService()
        with patch.object(graph_service, '_get_graph', return_value=None):
            result = gs.upsert_ioc("1.2.3.4", "ip")
            assert result is False

    def test_upsert_relation_returns_false_when_graph_none(self):
        """upsert_relation returns False when graph unavailable."""
        gs = GraphService()
        with patch.object(graph_service, '_get_graph', return_value=None):
            result = gs.upsert_relation("a.com", "b.com", "links_to")
            assert result is False

    def test_find_entity_history_returns_empty_when_graph_none(self):
        """find_entity_history returns [] when graph unavailable."""
        gs = GraphService()
        with patch.object(graph_service, '_get_graph', return_value=None):
            result = gs.find_entity_history("1.2.3.4")
            assert result == []

    def test_graph_stats_returns_empty_dict_when_graph_none(self):
        """graph_stats returns {} when graph unavailable."""
        gs = GraphService()
        with patch.object(graph_service, '_get_graph', return_value=None):
            result = gs.graph_stats()
            assert result == {}

    def test_checkpoint_noop_when_graph_none(self):
        """checkpoint is a no-op when graph unavailable."""
        gs = GraphService()
        with patch.object(graph_service, '_get_graph', return_value=None):
            gs.checkpoint()  # Should not raise

    def test_upsert_ioc_swallows_exception(self):
        """upsert_ioc returns False when graph.add_ioc raises."""
        gs = GraphService()
        mock_graph = MagicMock()
        mock_graph.add_ioc = MagicMock(side_effect=RuntimeError("DB err"))

        with patch.object(graph_service, '_get_graph', return_value=mock_graph):
            result = gs.upsert_ioc("1.2.3.4", "ip")
            assert result is False

    def test_upsert_identity_edge_delegates_to_upsert_relation(self):
        """upsert_identity_edge wraps upsert_relation with rel_type='same_identity'."""
        gs = GraphService()
        mock_graph = MagicMock()
        _DEFAULT_GRAPH_SERVICE._seen_rels.clear()

        with patch.object(graph_service, '_get_graph', return_value=mock_graph):
            r = gs.upsert_identity_edge("profile:A", "profile:B", 0.7, "same person")

            assert r is True
            mock_graph.add_relation.assert_called_once_with(
                "profile:A", "profile:B", "same_identity", 0.7, "same person"
            )

    def test_graph_analytics_summary_returns_empty_when_graph_none(self):
        """graph_analytics_summary returns fail-safe dict when graph unavailable."""
        gs = GraphService()
        with patch.object(graph_service, '_get_graph', return_value=None):
            result = gs.graph_analytics_summary()
            assert result["analytics_available"] is False
            assert result["skipped_reason"] == "graph_unavailable"