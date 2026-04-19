"""
Probe: ExportHandoff can be used as shadow input.

Sprint 8VK §Invariant: ExportHandoff (typed) is usable as shadow input
via collect_export_handoff_facts().

Sprint 8VY §A: Added TestCanonicalProducerHandoffPath — canonical runtime
path verification without new export framework.
"""


class TestExportHandoffAsShadowInput:
    """Verify ExportHandoff is consumable as shadow input."""

    def test_export_handoff_from_windup_is_usable(self):
        """ExportHandoff.from_windup() produces usable typed handoff."""
        from hledac.universal.project_types import ExportHandoff
        from hledac.universal.runtime.shadow_inputs import collect_export_handoff_facts

        scorecard = {
            "sprint_id": "sprint-456",
            "synthesis_engine_used": "Hermes3",
            "gnn_predicted_links": 3,
            "top_graph_nodes": ["node1", "node2"],
            "ranked_parquet": "/tmp/ranked.parquet",
            "phase_duration_seconds": {"ACTIVE": 100.0},
        }

        handoff = ExportHandoff.from_windup(
            sprint_id="sprint-456",
            scorecard=scorecard,
        )

        facts = collect_export_handoff_facts(handoff=handoff)

        assert facts["sprint_id"] == "sprint-456"
        assert facts["synthesis_engine"] == "Hermes3"
        assert facts["gnn_predictions"] == 3
        assert facts["top_nodes_count"] == 2
        assert facts["ranked_parquet_present"] is True
        assert facts["phase_durations"] == {"ACTIVE": 100.0}

    def test_export_handoff_vs_scorecard_parity(self):
        """ExportHandoff and scorecard dict produce equivalent facts."""
        from hledac.universal.project_types import ExportHandoff
        from hledac.universal.runtime.shadow_inputs import collect_export_handoff_facts

        scorecard = {
            "sprint_id": "sprint-789",
            "synthesis_engine_used": "MoE",
            "gnn_predicted_links": 7,
            "top_graph_nodes": [1, 2, 3, 4, 5],
            "ranked_parquet": None,
            "phase_duration_seconds": {"WINDUP": 5.0},
        }

        handoff = ExportHandoff.from_windup(
            sprint_id="sprint-789",
            scorecard=scorecard,
        )

        facts_from_handoff = collect_export_handoff_facts(handoff=handoff)
        facts_from_scorecard = collect_export_handoff_facts(scorecard=scorecard)

        assert facts_from_handoff["sprint_id"] == facts_from_scorecard["sprint_id"]
        assert facts_from_handoff["gnn_predictions"] == facts_from_scorecard["gnn_predictions"]
        assert facts_from_handoff["top_nodes_count"] == facts_from_scorecard["top_nodes_count"]

    def test_none_handoff_returns_defaults(self):
        """None handoff returns default values without crashing."""
        from hledac.universal.runtime.shadow_inputs import collect_export_handoff_facts

        facts = collect_export_handoff_facts(handoff=None, sprint_id="unknown")

        assert facts["sprint_id"] == "unknown"
        assert facts["synthesis_engine"] == "unknown"
        assert facts["gnn_predictions"] == 0
        assert facts["top_nodes_count"] == 0
        assert facts["ranked_parquet_present"] is False


class TestCanonicalProducerHandoffPath:
    """
    Sprint 8VY §A: Verify canonical runtime path uses typed ExportHandoff
    without primary reliance on from_windup(scorecard) dict extraction.

    Canonical path: __main__ → ExportHandoff.from_windup(sprint_id, scorecard_data)
    Compat path: scorecard["top_graph_nodes"] → top_nodes extraction (temporary seam).

    Invariant: ExportHandoff is constructed at producer side with typed fields;
    consumer (export_sprint) receives typed ExportHandoff, never needs to
    extract facts from raw scorecard dict.
    """

    def test_export_handoff_typed_construction(self):
        """ExportHandoff constructed directly from typed fields — no dict extraction needed."""
        from hledac.universal.project_types import ExportHandoff

        # Simulate what __main__ SHOULD do post-cutover:
        # windup returns typed ExportHandoff directly with pre-populated top_nodes
        handoff = ExportHandoff(
            sprint_id="sprint-test-001",
            scorecard={"synthesis_engine_used": "Hermes3", "gnn_predicted_links": 5},
            synthesis_engine="Hermes3",
            gnn_predictions=5,
            top_nodes=[{"value": "evil.com", "ioc_type": "domain"}],
            phase_durations={"ACTIVE": 120.0, "WINDUP": 3.0},
        )

        # typed fields directly accessible — no scorecard dict extraction needed
        assert handoff.sprint_id == "sprint-test-001"
        assert handoff.synthesis_engine == "Hermes3"
        assert handoff.gnn_predictions == 5
        assert len(handoff.top_nodes) == 1
        assert handoff.top_nodes[0]["value"] == "evil.com"
        assert handoff.phase_durations == {"ACTIVE": 120.0, "WINDUP": 3.0}

    def test_from_windup_is_compat_not_primary(self):
        """
        from_windup(scorecard) is COMPAT SEAM, not primary truth path.
        It extracts scorecard['top_graph_nodes'] → top_nodes because windup
        currently returns dict, not typed ExportHandoff.

        After windup cutover: __main__ calls ExportHandoff(...) directly,
        and from_windup(scorecard) becomes unnecessary.
        """
        from hledac.universal.project_types import ExportHandoff

        scorecard = {
            "sprint_id": "sprint-compat",
            "synthesis_engine_used": "MoE",
            "gnn_predicted_links": 7,
            "top_graph_nodes": [{"id": "node-1"}, {"id": "node-2"}],
            "phase_duration_seconds": {"WINDUP": 2.5},
        }

        # from_windup works today as compat extraction from dict
        handoff = ExportHandoff.from_windup(sprint_id="sprint-compat", scorecard=scorecard)

        # But top_nodes come from scorecard dict extraction — this is the compat seam
        assert handoff.top_nodes == [{"id": "node-1"}, {"id": "node-2"}]
        assert handoff.synthesis_engine == "MoE"

        # The scorecard dict is still preserved as-is (backward compat)
        assert handoff.scorecard["sprint_id"] == "sprint-compat"

    def test_ensure_export_handoff_passes_through_typed(self):
        """ensure_export_handoff() returns typed ExportHandoff unchanged — primary path."""
        from hledac.universal.project_types import ExportHandoff
        from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff

        handoff = ExportHandoff(
            sprint_id="sprint-typed",
            scorecard={},
            synthesis_engine="Hermes3",
            gnn_predictions=3,
            top_nodes=[{"value": "test.net", "ioc_type": "domain"}],
        )

        result = ensure_export_handoff(handoff, default_sprint_id="unknown")

        # Typed instance passed through unchanged — this IS the primary path
        assert result is handoff
        assert result.sprint_id == "sprint-typed"
        assert result.top_nodes == [{"value": "test.net", "ioc_type": "domain"}]

    def test_ensure_export_handoff_none_returns_empty(self):
        """
        None input returns empty ExportHandoff — compat seam B.
        REMOVAL CONDITION: __main__ always passes typed ExportHandoff, never None.
        """
        from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff

        result = ensure_export_handoff(None, default_sprint_id="sprint-none")

        assert result.sprint_id == "sprint-none"
        assert result.scorecard == {}
        assert result.top_nodes == []

    def test_ensure_export_handoff_dict_uses_from_windup(self):
        """
        dict input goes through from_windup(scorecard) — compat seam A.
        REMOVAL CONDITION: windup_engine returns typed ExportHandoff directly.
        """
        from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff

        scorecard = {
            "sprint_id": "sprint-dict",
            "synthesis_engine_used": "Llama",
            "gnn_predicted_links": 2,
            "top_graph_nodes": ["a", "b"],
        }

        result = ensure_export_handoff(scorecard, default_sprint_id="unknown")

        # dict is converted via from_windup() — this is the compat extraction seam
        assert result.sprint_id == "sprint-dict"
        assert result.synthesis_engine == "Llama"
        assert result.gnn_predictions == 2
        assert result.top_nodes == ["a", "b"]
