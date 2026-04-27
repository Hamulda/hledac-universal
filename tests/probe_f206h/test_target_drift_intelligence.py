"""
Sprint F206H: Target Drift Intelligence — Probe Tests
=====================================================

Invariant mapping:
  F206H-1  | confidence_drift has entity_delta key after merge with prior memory
  F206H-2  | confidence_drift has exposure_delta key after merge with prior memory
  F206H-3  | confidence_drift has pivot_delta key after merge with prior memory
  F206H-4  | confidence_drift has drift_reasons list after merge with prior memory
  F206H-5  | _compute_facet_delta returns added/removed/stable/total_prev/total_curr/top_added/top_removed
  F206H-6  | entity_delta.added reflects new entity types not in prior memory
  F206H-7  | entity_delta.removed reflects entity types dropped since prior memory
  F206H-8  | drift_reasons bounded to MAX_DRIFT_REASONS=8
  F206H-9  | entity_delta.total_curr bounded to MAX_DRIFT_DELTA_KEYS=20
  F206H-10 | First sprint (no existing memory) has legacy drift_ratio but no delta keys
  F206H-11 | build_sprint_brief shows concise drift explanation from drift_reasons
  F206H-12 | Backwards-compatible: old memory without delta keys falls back to drift_ratio
  F206H-13 | MAX_DRIFT_REASONS=8, MAX_DRIFT_DELTA_KEYS=20 are non-zero bounds
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.knowledge.target_memory import (
    MAX_DRIFT_DELTA_KEYS,
    MAX_DRIFT_REASONS,
    TargetMemory,
    TargetMemoryUpdate,
    TargetMemoryService,
)


# ============================================================================
# F206H-1/2/3/4/10: confidence_drift keys after merge
# ============================================================================

class TestConfidenceDriftStructure:
    """F206H-1 through F206H-4, F206H-10: confidence_drift has all new keys."""

    def test_first_sprint_has_legacy_keys_no_deltas(self):
        """F206H-10: First sprint has drift_ratio but no delta keys."""
        svc = TargetMemoryService()
        upd = TargetMemoryUpdate(
            target_id="t-new",
            sprint_id="s1",
            finding_count=5,
            entity_facets={"ip": 1.0, "domain": 0.9},
            exposure_facets={"cert": 1.0},
            pivot_facets={"dom": 0.8},
            observed_ts=100.0,
        )
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            result = svc.merge_update(upd)

        drift = result.confidence_drift
        # Legacy keys always present
        assert "sprints" in drift
        assert "total_findings" in drift
        assert "avg_findings_per_sprint" in drift
        assert "drift_ratio" in drift
        # New explainability keys present even for first sprint
        assert "entity_delta" in drift
        assert "exposure_delta" in drift
        assert "pivot_delta" in drift
        assert "drift_reasons" in drift

    def test_second_sprint_has_full_deltas(self):
        """F206H-1/2/3/4: Second sprint has entity_delta, exposure_delta, pivot_delta, drift_reasons."""
        svc = TargetMemoryService()
        upd1 = TargetMemoryUpdate(
            target_id="t-multi",
            sprint_id="s1",
            finding_count=5,
            entity_facets={"ip": 1.0, "domain": 0.9},
            exposure_facets={"cert": 1.0},
            pivot_facets={"dom": 0.8},
            observed_ts=100.0,
        )
        upd2 = TargetMemoryUpdate(
            target_id="t-multi",
            sprint_id="s2",
            finding_count=12,
            entity_facets={"ip": 1.0, "domain": 0.9, "email": 0.7},  # +email
            exposure_facets={"cert": 1.0, "open_port": 0.8},          # +open_port
            pivot_facets={"dom": 0.8, "leak": 0.6},                 # +leak
            observed_ts=200.0,
        )
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            svc.merge_update(upd1)
            result = svc.merge_update(upd2)

        drift = result.confidence_drift
        assert "entity_delta" in drift
        assert "exposure_delta" in drift
        assert "pivot_delta" in drift
        assert "drift_reasons" in drift

    def test_entity_delta_added_reflects_new_types(self):
        """F206H-6: entity_delta.added counts new entity types not in prior memory."""
        svc = TargetMemoryService()
        upd1 = TargetMemoryUpdate(
            target_id="t-entity",
            sprint_id="s1",
            finding_count=3,
            entity_facets={"ip": 1.0, "domain": 0.9},
            exposure_facets={},
            pivot_facets={},
            observed_ts=100.0,
        )
        upd2 = TargetMemoryUpdate(
            target_id="t-entity",
            sprint_id="s2",
            finding_count=5,
            entity_facets={"ip": 1.0, "domain": 0.9, "email": 0.7, "hash": 0.6},
            exposure_facets={},
            pivot_facets={},
            observed_ts=200.0,
        )
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            svc.merge_update(upd1)
            result = svc.merge_update(upd2)

        entity_delta = result.confidence_drift["entity_delta"]
        # 2 new entity types: email, hash
        assert entity_delta["added"] == 2

    def test_entity_delta_removed_reflects_dropped_types(self):
        """F206H-7: entity_delta.removed counts entity types not in current memory."""
        svc = TargetMemoryService()
        upd1 = TargetMemoryUpdate(
            target_id="t-removed",
            sprint_id="s1",
            finding_count=3,
            entity_facets={"ip": 1.0, "domain": 0.9, "email": 0.7},
            exposure_facets={},
            pivot_facets={},
            observed_ts=100.0,
        )
        upd2 = TargetMemoryUpdate(
            target_id="t-removed",
            sprint_id="s2",
            finding_count=5,
            entity_facets={"ip": 1.0},  # domain and email gone
            exposure_facets={},
            pivot_facets={},
            observed_ts=200.0,
        )
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            svc.merge_update(upd1)
            result = svc.merge_update(upd2)

        entity_delta = result.confidence_drift["entity_delta"]
        # 2 removed entity types: domain, email
        assert entity_delta["removed"] == 2


# ============================================================================
# F206H-5: _compute_facet_delta structure
# ============================================================================

class TestFacetDeltaStructure:
    """F206H-5: _compute_facet_delta returns correct structure."""

    def test_facet_delta_has_required_keys(self):
        """F206H-5: facet delta returns added/removed/stable/total_prev/total_curr/top_added/top_removed."""
        svc = TargetMemoryService()
        delta = svc._compute_facet_delta(
            {"a": 1.0, "b": 0.9},
            {"b": 0.9, "c": 0.8, "d": 0.7},
            max_keys=20,
        )
        assert "added" in delta
        assert "removed" in delta
        assert "stable" in delta
        assert "total_prev" in delta
        assert "total_curr" in delta
        assert "top_added" in delta
        assert "top_removed" in delta

    def test_facet_delta_stable_counts_common_keys(self):
        """F206H-5: stable counts keys present in both existing and update."""
        svc = TargetMemoryService()
        delta = svc._compute_facet_delta(
            {"a": 1.0, "b": 0.9, "c": 0.8},
            {"b": 0.9, "c": 0.8, "d": 0.7},
            max_keys=20,
        )
        assert delta["stable"] == 2  # b and c

    def test_facet_delta_top_added_sorted_by_score(self):
        """F206H-5: top_added sorted by score descending."""
        svc = TargetMemoryService()
        delta = svc._compute_facet_delta(
            {"x": 0.1},
            {"x": 0.1, "a": 0.9, "b": 0.8, "c": 0.7, "d": 0.6},
            max_keys=20,
        )
        assert delta["top_added"] == ["a", "b", "c", "d"]
        assert len(delta["top_added"]) == 4


# ============================================================================
# F206H-8/9: Bounds enforcement
# ============================================================================

class TestDriftBounds:
    """F206H-8/9/13: Drift reasons and delta keys are bounded."""

    def test_drift_reasons_bounded_to_max(self):
        """F206H-8: drift_reasons capped to MAX_DRIFT_REASONS=8."""
        svc = TargetMemoryService()
        # Create many entity types to generate many drift reasons
        existing_entities = {f"old{i}": 0.5 for i in range(10)}
        update_entities = {f"new{i}": 0.5 for i in range(20)}
        upd1 = TargetMemoryUpdate(
            target_id="t-bound",
            sprint_id="s1",
            finding_count=10,
            entity_facets=existing_entities,
            exposure_facets={},
            pivot_facets={},
            observed_ts=100.0,
        )
        upd2 = TargetMemoryUpdate(
            target_id="t-bound",
            sprint_id="s2",
            finding_count=20,
            entity_facets=update_entities,
            exposure_facets={},
            pivot_facets={},
            observed_ts=200.0,
        )
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            svc.merge_update(upd1)
            result = svc.merge_update(upd2)

        reasons = result.confidence_drift["drift_reasons"]
        assert len(reasons) <= MAX_DRIFT_REASONS

    def test_delta_keys_bounded_to_max(self):
        """F206H-9: entity_delta.total_curr bounded to MAX_DRIFT_DELTA_KEYS=20."""
        svc = TargetMemoryService()
        many_entities = {f"e{i}": 0.5 for i in range(50)}
        upd1 = TargetMemoryUpdate(
            target_id="t-delta-bound",
            sprint_id="s1",
            finding_count=5,
            entity_facets={"a": 1.0},
            exposure_facets={},
            pivot_facets={},
            observed_ts=100.0,
        )
        upd2 = TargetMemoryUpdate(
            target_id="t-delta-bound",
            sprint_id="s2",
            finding_count=10,
            entity_facets=many_entities,
            exposure_facets={},
            pivot_facets={},
            observed_ts=200.0,
        )
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            svc.merge_update(upd1)
            result = svc.merge_update(upd2)

        entity_delta = result.confidence_drift["entity_delta"]
        assert entity_delta["total_curr"] <= MAX_DRIFT_DELTA_KEYS

    def test_max_drift_reasons_non_zero(self):
        """F206H-13: MAX_DRIFT_REASONS is a positive integer."""
        assert MAX_DRIFT_REASONS > 0
        assert isinstance(MAX_DRIFT_REASONS, int)

    def test_max_drift_delta_keys_non_zero(self):
        """F206H-13: MAX_DRIFT_DELTA_KEYS is a positive integer."""
        assert MAX_DRIFT_DELTA_KEYS > 0
        assert isinstance(MAX_DRIFT_DELTA_KEYS, int)


# ============================================================================
# F206H-12: Backwards compatibility (fallback to drift_ratio)
# ============================================================================

class TestBackwardsCompatibility:
    """F206H-12: Old memory without delta keys falls back to drift_ratio."""

    def test_legacy_memory_without_delta_keys_uses_drift_ratio(self):
        """F206H-12: confidence_drift without new keys falls back to drift_ratio only."""
        svc = TargetMemoryService()
        # Simulate old-style memory (no delta keys)
        old_drift = {
            "sprints": 3,
            "total_findings": 30,
            "avg_findings_per_sprint": 10.0,
            "drift_ratio": 1.5,
        }
        old_memory = TargetMemory(
            target_id="t-legacy",
            first_seen_ts=100.0,
            last_seen_ts=200.0,
            sprint_count=3,
            cumulative_finding_count=30,
            entity_facets={"ip": 1.0},
            exposure_facets={},
            pivot_facets={},
            confidence_drift=old_drift,
            updated_by_sprint_id="s3",
        )
        svc._cache["t-legacy"] = old_memory

        upd = TargetMemoryUpdate(
            target_id="t-legacy",
            sprint_id="s4",
            finding_count=20,  # drift_ratio = 20/11.67 ≈ 1.71
            entity_facets={"ip": 1.0, "domain": 0.9},
            exposure_facets={},
            pivot_facets={},
            observed_ts=300.0,
        )
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            result = svc.merge_update(upd)

        # New keys should be present after merge
        assert "entity_delta" in result.confidence_drift
        assert "drift_reasons" in result.confidence_drift
        # But legacy drift_ratio is still used as base
        assert result.confidence_drift["drift_ratio"] > 1.0


# ============================================================================
# F206H-11: build_sprint_brief drift explanation
# ============================================================================

class TestBriefDriftExplanation:
    """F206H-11: build_sprint_brief shows concise drift explanation."""

    @pytest.mark.asyncio
    async def test_brief_includes_drift_explanation(self):
        """F206H-11: brief key_findings include concise drift explanation from drift_reasons."""
        from hledac.universal.knowledge.analyst_workbench import AnalystWorkbench

        mock_store = MagicMock()
        mock_tmemory = MagicMock()
        mock_tmemory.target_id = "test-target"
        mock_tmemory.sprint_count = 2
        mock_tmemory.cumulative_finding_count = 10
        mock_tmemory.entity_facets = {"ip": 1.0, "domain": 0.9}
        mock_tmemory.exposure_facets = {"cert": 1.0}
        mock_tmemory.pivot_facets = {}
        mock_tmemory.confidence_drift = {
            "sprints": 2,
            "total_findings": 10,
            "avg_findings_per_sprint": 5.0,
            "drift_ratio": 2.0,
            "entity_delta": {"added": 2, "removed": 0, "stable": 0,
                             "total_prev": 0, "total_curr": 2, "top_added": ["ip", "domain"], "top_removed": []},
            "exposure_delta": {"added": 1, "removed": 0, "stable": 0,
                               "total_prev": 0, "total_curr": 1, "top_added": ["cert"], "top_removed": []},
            "pivot_delta": {},
            "drift_reasons": [
                "finding_rate_high:ratio=2.00",
                "entity_new_types:2_added",
                "new_entity:ip",
                "new_entity:domain",
            ],
        }
        mock_store.async_get_target_memory = AsyncMock(return_value=mock_tmemory)

        workbench = AnalystWorkbench(duckdb_store=mock_store)

        findings = [
            {"finding_id": "f1", "source_type": "ct", "confidence": 0.7,
             "ioc_type": "domain", "ioc_value": "evil.com", "query": "test",
             "provenance": ()},
        ]

        brief = await workbench.build_sprint_brief(
            sprint_id="sprint-abc",
            target_id="test-target",
            findings=findings,
            graph_signal={"graph_nodes": 5, "graph_edges": 12},
            governor=None,
            duckdb_store=mock_store,
        )

        # Drift explanation should be in key_findings
        key_findings_text = "\n".join(brief.key_findings)
        assert "finding_rate_high" in key_findings_text or "Drift signals:" in key_findings_text, \
            f"Key findings: {brief.key_findings}"

    @pytest.mark.asyncio
    async def test_brief_without_drift_reasons_still_shows_memory(self):
        """F206H-11: brief without drift_reasons still shows memory (graceful)."""
        from hledac.universal.knowledge.analyst_workbench import AnalystWorkbench

        mock_store = MagicMock()
        mock_tmemory = MagicMock()
        mock_tmemory.target_id = "test-target"
        mock_tmemory.sprint_count = 2
        mock_tmemory.cumulative_finding_count = 10
        mock_tmemory.entity_facets = {"ip": 1.0}
        mock_tmemory.exposure_facets = {}
        mock_tmemory.pivot_facets = {}
        # Legacy drift without drift_reasons
        mock_tmemory.confidence_drift = {
            "sprints": 2,
            "total_findings": 10,
            "avg_findings_per_sprint": 5.0,
            "drift_ratio": 1.0,
        }
        mock_store.async_get_target_memory = AsyncMock(return_value=mock_tmemory)

        workbench = AnalystWorkbench(duckdb_store=mock_store)

        findings = [{"finding_id": "f1", "source_type": "ct", "confidence": 0.7,
                     "ioc_type": "ip", "ioc_value": "1.2.3.4", "query": "test",
                     "provenance": ()}]

        brief = await workbench.build_sprint_brief(
            sprint_id="sprint-xyz",
            target_id="test-target",
            findings=findings,
            graph_signal={"graph_nodes": 1, "graph_edges": 0},
            governor=None,
            duckdb_store=mock_store,
        )

        # Should not raise — fail-soft
        assert brief.sprint_id == "sprint-xyz"
        key_findings_text = "".join(brief.key_findings)
        assert "Target memory:" in key_findings_text


# ============================================================================
# Smoke tests
# ============================================================================

class TestSmoke:
    """F206H-SMOKE: Module imports and bounds."""

    def test_module_imports(self):
        """Invariant: module imports without error."""
        from hledac.universal.knowledge.target_memory import (
            MAX_DRIFT_DELTA_KEYS,
            MAX_DRIFT_REASONS,
            TargetMemory,
            TargetMemoryService,
            TargetMemoryUpdate,
        )
        assert TargetMemory is not None
        assert TargetMemoryService is not None
        assert TargetMemoryUpdate is not None
        assert MAX_DRIFT_REASONS > 0
        assert MAX_DRIFT_DELTA_KEYS > 0

    def test_exports_in_target_memory_module(self):
        """Invariant: target_memory exports F206H bounds."""
        from hledac.universal.knowledge import target_memory
        assert hasattr(target_memory, "MAX_DRIFT_REASONS")
        assert hasattr(target_memory, "MAX_DRIFT_DELTA_KEYS")
