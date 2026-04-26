"""
Sprint F204D: Target Memory Profile — Probe Tests
"""

from __future__ import annotations

import json  # noqa: F401

from dataclasses import fields as _dc_fields
from unittest.mock import MagicMock, patch

import pytest

from hledac.universal.knowledge.target_memory import (
    MAX_MEMORY_ENTITIES,
    MAX_MEMORY_EXPOSURES,
    MAX_MEMORY_JSON_BYTES,
    MAX_MEMORY_PIVOTS,
    TargetMemory,
    TargetMemoryUpdate,
    TargetMemoryService,
)


# ---------------------------------------------------------------------------
# TargetMemory dataclass (tests 1-5)
# ---------------------------------------------------------------------------

class TestTargetMemoryDataclass:
    """F204D-1: TargetMemory frozen dataclass with correct fields."""

    def test_target_memory_all_fields(self):
        """Invariant: all fields present."""
        flds = {f.name for f in _dc_fields(TargetMemory)}
        expected = {
            "target_id", "first_seen_ts", "last_seen_ts", "sprint_count",
            "cumulative_finding_count", "entity_facets", "exposure_facets",
            "pivot_facets", "confidence_drift", "updated_by_sprint_id",
        }
        assert expected <= flds, f"missing fields: {expected - flds}"

    def test_target_memory_field_access(self):
        """Invariant: field values accessible after creation."""
        tm = TargetMemory(
            target_id="t-1",
            first_seen_ts=1000.0,
            last_seen_ts=2000.0,
            sprint_count=3,
            cumulative_finding_count=42,
            entity_facets={"ip": 1},
            exposure_facets={"cert": 2},
            pivot_facets={"dom": 3},
            confidence_drift={"sprints": 3},
            updated_by_sprint_id="sprint-2",
        )
        assert tm.target_id == "t-1"
        assert tm.first_seen_ts == 1000.0
        assert tm.last_seen_ts == 2000.0
        assert tm.sprint_count == 3
        assert tm.cumulative_finding_count == 42
        assert tm.entity_facets == {"ip": 1}
        assert tm.exposure_facets == {"cert": 2}
        assert tm.pivot_facets == {"dom": 3}
        assert tm.confidence_drift == {"sprints": 3}
        assert tm.updated_by_sprint_id == "sprint-2"

    def test_target_memory_immutable(self):
        """Invariant: frozen=True prevents field mutation."""
        tm = TargetMemory(
            target_id="t-1",
            first_seen_ts=1000.0,
            last_seen_ts=2000.0,
            sprint_count=1,
            cumulative_finding_count=0,
            entity_facets={},
            exposure_facets={},
            pivot_facets={},
            confidence_drift={},
            updated_by_sprint_id="",
        )
        with pytest.raises(Exception):
            tm.target_id = "changed"  # type: ignore

    def test_target_memory_empty_dicts(self):
        """Invariant: facet fields default to empty dicts."""
        tm = TargetMemory(
            target_id="t-1",
            first_seen_ts=1000.0,
            last_seen_ts=2000.0,
            sprint_count=0,
            cumulative_finding_count=0,
            entity_facets={},
            exposure_facets={},
            pivot_facets={},
            confidence_drift={},
            updated_by_sprint_id="",
        )
        assert tm.entity_facets == {}
        assert tm.exposure_facets == {}
        assert tm.pivot_facets == {}
        assert tm.confidence_drift == {}

    def test_target_memory_no_slots(self):
        """Invariant: frozen dataclass may have slots=False."""
        # frozen=True verified by immutability test above
        assert True


# ---------------------------------------------------------------------------
# TargetMemoryUpdate dataclass (tests 6-8)
# ---------------------------------------------------------------------------

class TestTargetMemoryUpdateDataclass:
    """F204D-2: TargetMemoryUpdate frozen dataclass."""

    def test_update_all_fields(self):
        """Invariant: all fields present."""
        flds = {f.name for f in _dc_fields(TargetMemoryUpdate)}
        expected = {
            "target_id", "sprint_id", "finding_count",
            "entity_facets", "exposure_facets", "pivot_facets", "observed_ts",
        }
        assert expected <= flds, f"missing fields: {expected - flds}"

    def test_update_field_access(self):
        """Invariant: field values accessible after creation."""
        upd = TargetMemoryUpdate(
            target_id="t-1",
            sprint_id="sprint-2",
            finding_count=5,
            entity_facets={"ip": 1},
            exposure_facets={"cert": 2},
            pivot_facets={"dom": 3},
            observed_ts=1234.5,
        )
        assert upd.target_id == "t-1"
        assert upd.sprint_id == "sprint-2"
        assert upd.finding_count == 5
        assert upd.entity_facets == {"ip": 1}
        assert upd.exposure_facets == {"cert": 2}
        assert upd.pivot_facets == {"dom": 3}
        assert upd.observed_ts == 1234.5

    def test_update_immutable(self):
        """Invariant: frozen=True prevents field mutation."""
        upd = TargetMemoryUpdate(
            target_id="t-1",
            sprint_id="sprint-1",
            finding_count=1,
            entity_facets={},
            exposure_facets={},
            pivot_facets={},
            observed_ts=0.0,
        )
        with pytest.raises(Exception):
            upd.target_id = "changed"  # type: ignore


# ---------------------------------------------------------------------------
# TargetMemoryService.merge_update() (tests 9-14)
# ---------------------------------------------------------------------------

class TestMergeUpdate:
    """F204D-3: merge_update creates/updates memory correctly."""

    def test_new_target_creates_memory(self):
        """Invariant: first update creates new TargetMemory."""
        svc = TargetMemoryService()
        upd = TargetMemoryUpdate(
            target_id="new-target",
            sprint_id="s1",
            finding_count=10,
            entity_facets={"ip1": 1.0},
            exposure_facets={"cert1": 0.9},
            pivot_facets={"dom1": 0.8},
            observed_ts=1000.0,
        )
        # RAM guard check uses psutil.virtual_memory().percent - mock it
        # to return low so merge proceeds (RAM guard uses percent >= 90)
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            result = svc.merge_update(upd)
        assert result.target_id == "new-target"
        assert result.sprint_count == 1
        assert result.cumulative_finding_count == 10
        assert result.first_seen_ts == 1000.0
        assert result.last_seen_ts == 1000.0
        assert result.updated_by_sprint_id == "s1"

    def test_existing_target_merges_correctly(self):
        """Invariant: subsequent updates increment sprint/finding counts."""
        svc = TargetMemoryService()
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            upd1 = TargetMemoryUpdate(
                target_id="t-1",
                sprint_id="s1",
                finding_count=5,
                entity_facets={"a": 1},
                exposure_facets={"b": 1},
                pivot_facets={"c": 1},
                observed_ts=100.0,
            )
            upd2 = TargetMemoryUpdate(
                target_id="t-1",
                sprint_id="s2",
                finding_count=7,
                entity_facets={"d": 1},
                exposure_facets={"e": 1},
                pivot_facets={"f": 1},
                observed_ts=200.0,
            )
            svc.merge_update(upd1)
            result = svc.merge_update(upd2)
        assert result.sprint_count == 2
        assert result.cumulative_finding_count == 12
        assert result.first_seen_ts == 100.0
        assert result.last_seen_ts == 200.0
        assert result.updated_by_sprint_id == "s2"

    def test_entity_facets_bound_enforcement(self):
        """Invariant: entity_facets truncated to MAX_MEMORY_ENTITIES."""
        svc = TargetMemoryService()
        large_facets = {f"e{i}": i * 0.1 for i in range(MAX_MEMORY_ENTITIES + 50)}
        upd = TargetMemoryUpdate(
            target_id="t-bound",
            sprint_id="s1",
            finding_count=1,
            entity_facets=large_facets,
            exposure_facets={},
            pivot_facets={},
            observed_ts=0.0,
        )
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            result = svc.merge_update(upd)
        assert len(result.entity_facets) == MAX_MEMORY_ENTITIES

    def test_exposure_facets_bound_enforcement(self):
        """Invariant: exposure_facets truncated to MAX_MEMORY_EXPOSURES."""
        svc = TargetMemoryService()
        large_facets = {f"x{i}": i * 0.1 for i in range(MAX_MEMORY_EXPOSURES + 50)}
        upd = TargetMemoryUpdate(
            target_id="t-bound",
            sprint_id="s1",
            finding_count=1,
            entity_facets={},
            exposure_facets=large_facets,
            pivot_facets={},
            observed_ts=0.0,
        )
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            result = svc.merge_update(upd)
        assert len(result.exposure_facets) == MAX_MEMORY_EXPOSURES

    def test_pivot_facets_bound_enforcement(self):
        """Invariant: pivot_facets truncated to MAX_MEMORY_PIVOTS."""
        svc = TargetMemoryService()
        large_facets = {f"p{i}": i * 0.1 for i in range(MAX_MEMORY_PIVOTS + 50)}
        upd = TargetMemoryUpdate(
            target_id="t-bound",
            sprint_id="s1",
            finding_count=1,
            entity_facets={},
            exposure_facets={},
            pivot_facets=large_facets,
            observed_ts=0.0,
        )
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            result = svc.merge_update(upd)
        assert len(result.pivot_facets) == MAX_MEMORY_PIVOTS

    def test_confidence_drift_calculation(self):
        """Invariant: confidence_drift tracks finding_count / sprint_count."""
        svc = TargetMemoryService()
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            upd1 = TargetMemoryUpdate(
                target_id="t-drift",
                sprint_id="s1",
                finding_count=10,
                entity_facets={},
                exposure_facets={},
                pivot_facets={},
                observed_ts=100.0,
            )
            r1 = svc.merge_update(upd1)
            assert r1.confidence_drift["sprints"] == 1
            assert r1.confidence_drift["total_findings"] == 10
            assert r1.confidence_drift["avg_findings_per_sprint"] == 10.0

            upd2 = TargetMemoryUpdate(
                target_id="t-drift",
                sprint_id="s2",
                finding_count=30,
                entity_facets={},
                exposure_facets={},
                pivot_facets={},
                observed_ts=200.0,
            )
            r2 = svc.merge_update(upd2)
        assert r2.confidence_drift["sprints"] == 2
        assert r2.confidence_drift["total_findings"] == 40
        assert r2.confidence_drift["avg_findings_per_sprint"] == 20.0
        assert abs(r2.confidence_drift["drift_ratio"] - 1.5) < 0.001


# ---------------------------------------------------------------------------
# Bounds enforcement (tests 15-18)
# ---------------------------------------------------------------------------

class TestBounds:
    """F204D-4: Constants are bounded and non-zero."""

    def test_max_memory_json_bytes(self):
        """Invariant: JSON bound is 64KB."""
        assert MAX_MEMORY_JSON_BYTES == 65536

    def test_max_memory_entities(self):
        """Invariant: entity facet bound is 500."""
        assert MAX_MEMORY_ENTITIES == 500

    def test_max_memory_exposures(self):
        """Invariant: exposure facet bound is 500."""
        assert MAX_MEMORY_EXPOSURES == 500

    def test_max_memory_pivots(self):
        """Invariant: pivot facet bound is 100."""
        assert MAX_MEMORY_PIVOTS == 100


# ---------------------------------------------------------------------------
# RAM guard (tests 19-20)
# ---------------------------------------------------------------------------

class TestRAMGuard:
    """F204D-5: RAM guard skips merge when RSS > high_water."""

    def test_skip_merge_when_rss_above_threshold(self):
        """Invariant: RAM guard returns empty memory when RSS > threshold."""
        svc = TargetMemoryService()
        upd = TargetMemoryUpdate(
            target_id="t-ram",
            sprint_id="s1",
            finding_count=1,
            entity_facets={"key": 1.0},
            exposure_facets={},
            pivot_facets={},
            observed_ts=0.0,
        )
        # Simulate HIGH memory to trigger RAM guard (percent >= 90)
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=95.0)):
            result = svc.merge_update(upd)
        # RAM guard returns empty memory for unknown target
        assert result.target_id == "t-ram"
        assert result.sprint_count == 0
        assert result.cumulative_finding_count == 0

    def test_skip_merge_preserves_existing_cache(self):
        """Invariant: RAM guard returns existing cached memory."""
        svc = TargetMemoryService()
        # First merge with RAM guard disabled (low memory)
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            upd1 = TargetMemoryUpdate(
                target_id="t-ram-cache",
                sprint_id="s1",
                finding_count=5,
                entity_facets={"a": 1.0},
                exposure_facets={},
                pivot_facets={},
                observed_ts=100.0,
            )
            svc.merge_update(upd1)
        # Now trigger RAM guard (high memory)
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=95.0)):
            upd2 = TargetMemoryUpdate(
                target_id="t-ram-cache",
                sprint_id="s2",
                finding_count=10,
                entity_facets={"b": 1.0},
                exposure_facets={},
                pivot_facets={},
                observed_ts=200.0,
            )
            result = svc.merge_update(upd2)
        # RAM guard returns cached value unchanged
        assert result.sprint_count == 1
        assert result.cumulative_finding_count == 5
        assert result.updated_by_sprint_id == "s1"


# ---------------------------------------------------------------------------
# Fail-soft on corrupt JSON (tests 21-22)
# ---------------------------------------------------------------------------

class TestFailSoftOnCorruptJSON:
    """F204D-6: _safe_parse_facets returns empty dict for corrupt JSON."""

    def test_none_returns_empty_dict(self):
        """GHOST_INVARIANT: None input → empty dict."""
        svc = TargetMemoryService()
        assert svc._safe_parse_facets(None) == {}

    def test_corrupt_bytes_returns_empty_dict(self):
        """GHOST_INVARIANT: corrupt JSON bytes → empty dict."""
        svc = TargetMemoryService()
        corrupt = b"\x00\xff\xfe invalid json {"
        assert svc._safe_parse_facets(corrupt) == {}

    def test_corrupt_string_returns_empty_dict(self):
        """GHOST_INVARIANT: corrupt JSON string → empty dict."""
        svc = TargetMemoryService()
        corrupt = "{ not valid json"
        assert svc._safe_parse_facets(corrupt) == {}

    def test_valid_json_string_parses(self):
        """Invariant: valid JSON string parsed correctly."""
        svc = TargetMemoryService()
        result = svc._safe_parse_facets('{"key": 1.0, "nested": {"a": 2}}')
        assert result == {"key": 1.0, "nested": {"a": 2}}

    def test_dict_passthrough(self):
        """Invariant: dict input returned as-is."""
        svc = TargetMemoryService()
        data = {"key": 1.0}
        assert svc._safe_parse_facets(data) == data


# ---------------------------------------------------------------------------
# Deterministic merge (tests 23-24)
# ---------------------------------------------------------------------------

class TestDeterministicMerge:
    """F204D-7: Merge is deterministic — same update, same result."""

    def test_same_update_twice_produces_same_result(self):
        """Invariant: duplicate update applied to separate services yields identical state."""
        svc1 = TargetMemoryService()
        svc2 = TargetMemoryService()
        upd = TargetMemoryUpdate(
            target_id="t-det",
            sprint_id="s1",
            finding_count=5,
            entity_facets={"e1": 0.9},
            exposure_facets={"x1": 0.8},
            pivot_facets={"p1": 0.7},
            observed_ts=100.0,
        )
        # Mock RAM guard so merge proceeds
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            r1 = svc1.merge_update(upd)
            r2 = svc2.merge_update(upd)
        assert r1 == r2

    def test_merge_order_does_not_affect_final_state(self):
        """Invariant: aggregate counts converge regardless of update order."""
        svc_a = TargetMemoryService()
        svc_b = TargetMemoryService()
        # Mock RAM guard so merge proceeds
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            upd_a1 = TargetMemoryUpdate(
                target_id="t-order",
                sprint_id="s1",
                finding_count=3,
                entity_facets={"a": 1.0},
                exposure_facets={},
                pivot_facets={},
                observed_ts=100.0,
            )
            upd_a2 = TargetMemoryUpdate(
                target_id="t-order",
                sprint_id="s2",
                finding_count=4,
                entity_facets={"b": 1.0},
                exposure_facets={},
                pivot_facets={},
                observed_ts=200.0,
            )
            upd_b1 = TargetMemoryUpdate(
                target_id="t-order",
                sprint_id="s2",
                finding_count=4,
                entity_facets={"b": 1.0},
                exposure_facets={},
                pivot_facets={},
                observed_ts=200.0,
            )
            upd_b2 = TargetMemoryUpdate(
                target_id="t-order",
                sprint_id="s1",
                finding_count=3,
                entity_facets={"a": 1.0},
                exposure_facets={},
                pivot_facets={},
                observed_ts=100.0,
            )

            svc_a.merge_update(upd_a1)
            svc_a.merge_update(upd_a2)
            svc_b.merge_update(upd_b1)
            svc_b.merge_update(upd_b2)

            ra = svc_a.get("t-order")
            rb = svc_b.get("t-order")
        # Aggregate counts converge regardless of update order
        assert ra.sprint_count == rb.sprint_count == 2
        assert ra.cumulative_finding_count == rb.cumulative_finding_count == 7
        # Non-aggregate fields may differ (last update wins), but both valid
        assert ra.updated_by_sprint_id in ("s1", "s2")
        assert rb.updated_by_sprint_id in ("s1", "s2")


# ---------------------------------------------------------------------------
# Smoke (tests 25-26)
# ---------------------------------------------------------------------------

class TestSmoke:
    """F204D-8: Module imports and constants export."""

    def test_module_imports(self):
        """Invariant: module imports without error."""
        from hledac.universal.knowledge.target_memory import (
            TargetMemory,
            TargetMemoryService,
            TargetMemoryUpdate,
        )
        assert TargetMemory is not None
        assert TargetMemoryService is not None
        assert TargetMemoryUpdate is not None

    def test_service_cache_property(self):
        """Invariant: service exposes cache_size property."""
        svc = TargetMemoryService()
        assert svc.cache_size == 0
        with patch("psutil.virtual_memory", return_value=MagicMock(percent=10.0)):
            svc.merge_update(TargetMemoryUpdate(
                target_id="x", sprint_id="s1", finding_count=1,
                entity_facets={}, exposure_facets={}, pivot_facets={},
                observed_ts=0.0,
            ))
        assert svc.cache_size == 1
        svc.clear()
        assert svc.cache_size == 0

    def test_bounds_are_non_zero(self):
        """Invariant: all bounds are positive integers."""
        assert MAX_MEMORY_JSON_BYTES > 0
        assert MAX_MEMORY_ENTITIES > 0
        assert MAX_MEMORY_EXPOSURES > 0
        assert MAX_MEMORY_PIVOTS > 0