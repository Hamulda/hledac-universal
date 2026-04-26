"""
Sprint F203A: Sprint Diff Engine and Target Profiles — Probe Tests
==================================================================

Invariant mapping:
  F203A-1  | SprintDiffEngine.compute_diff returns new/disappeared/changed findings
  F203A-2  | First sprint (previous=None): all findings are new, none disappeared
  F203A-3  | MAX_DIFF_FINDINGS=100 cap enforced on new/disappeared/changed lists
  F203A-4  | Entity key = (ioc_type, ioc_value) for deduplication
  F203A-5  | Changed entity: same ioc_value but different ioc_type or finding_id
  F203A-6  | TargetProfileSummary.first_seen / last_seen correctly tracked
  F203A-7  | Finding velocity = cumulative / days_since_first_seen
  F203A-8  | entity_summary_json is valid JSON with entity type breakdown
  F203A-9  | Fail-soft: malformed findings (missing ioc_type/ioc_value) are skipped
"""

import json
import time

import pytest

from hledac.universal.knowledge.sprint_diff_engine import (
    MAX_DIFF_FINDINGS,
    MAX_PROFILE_ENTRIES,
    SprintDiffEngine,
    SprintDiffResult,
    TargetProfileSummary,
)


# ============================================================================
# F203A-1/3: SprintDiffEngine core diff computation
# ============================================================================

class TestSprintDiffEngine:
    """F203A-1: SprintDiffEngine core diff computation."""

    def test_diff_new_findings_when_no_previous(self):
        """First sprint for a target: all findings are new."""
        engine = SprintDiffEngine()
        current = [
            {"ioc_type": "domain", "ioc_value": "evil.com", "finding_id": "f1"},
            {"ioc_type": "ip", "ioc_value": "1.2.3.4", "finding_id": "f2"},
        ]
        result = engine.compute_diff(current, None, "target1", "sprint-2", None)

        assert result.target_id == "target1"
        assert result.current_sprint_id == "sprint-2"
        assert result.previous_sprint_id is None
        assert len(result.new_findings) == 2
        assert len(result.disappeared_findings) == 0
        assert len(result.changed_entities) == 0

    def test_diff_disappeared_findings(self):
        """Findings from previous sprint that are missing now = disappeared."""
        engine = SprintDiffEngine()
        previous = [
            {"ioc_type": "domain", "ioc_value": "old.com", "finding_id": "f_old"},
        ]
        current = [
            {"ioc_type": "domain", "ioc_value": "new.com", "finding_id": "f_new"},
        ]
        result = engine.compute_diff(current, previous, "target1", "sprint-2", "sprint-1")

        assert result.previous_sprint_id == "sprint-1"
        assert len(result.new_findings) == 1
        assert len(result.disappeared_findings) == 1
        assert result.disappeared_findings[0]["ioc_value"] == "old.com"

    def test_diff_changed_entity(self):
        """Same IOC value but different type = changed entity."""
        engine = SprintDiffEngine()
        previous = [
            {"ioc_type": "domain", "ioc_value": ".example.com", "finding_id": "f1"},
        ]
        current = [
            # Same value, different type
            {"ioc_type": "ip", "ioc_value": ".example.com", "finding_id": "f2"},
        ]
        result = engine.compute_diff(current, previous, "target1", "sprint-2", "sprint-1")

        assert len(result.changed_entities) == 1

    def test_diff_bounds_new_findings(self):
        """MAX_DIFF_FINDINGS=100 cap is enforced."""
        engine = SprintDiffEngine()
        current = [
            {"ioc_type": "domain", "ioc_value": f"new{i}.com", "finding_id": f"f{i}"}
            for i in range(150)
        ]
        result = engine.compute_diff(current, None, "target1", "sprint-2", None)

        assert len(result.new_findings) == 100  # capped

    def test_diff_same_findings_unchanged(self):
        """Same IOC values in current and previous = unchanged (not in any diff list)."""
        engine = SprintDiffEngine()
        previous = [
            {"ioc_type": "domain", "ioc_value": "same.com", "finding_id": "f1"},
        ]
        current = [
            {"ioc_type": "domain", "ioc_value": "same.com", "finding_id": "f1"},
        ]
        result = engine.compute_diff(current, previous, "target1", "sprint-2", "sprint-1")

        assert len(result.new_findings) == 0
        assert len(result.disappeared_findings) == 0
        assert len(result.changed_entities) == 0

    def test_diff_same_ioc_value_different_finding_id_counts_as_changed(self):
        """Same entity (ioc_type + ioc_value) but different finding_id = changed."""
        engine = SprintDiffEngine()
        previous = [
            {"ioc_type": "domain", "ioc_value": "example.com", "finding_id": "old_f1"},
        ]
        current = [
            {"ioc_type": "domain", "ioc_value": "example.com", "finding_id": "new_f1"},
        ]
        result = engine.compute_diff(current, previous, "target1", "sprint-2", "sprint-1")

        assert len(result.changed_entities) == 1
        assert result.changed_entities[0]["before"]["finding_id"] == "old_f1"
        assert result.changed_entities[0]["after"]["finding_id"] == "new_f1"

    def test_diff_entity_key_case_insensitive(self):
        """Entity keys are case-insensitive for ioc_value."""
        engine = SprintDiffEngine()
        previous = [
            {"ioc_type": "domain", "ioc_value": "Example.COM", "finding_id": "f1"},
        ]
        current = [
            {"ioc_type": "domain", "ioc_value": "example.com", "finding_id": "f1"},  # same finding_id
        ]
        result = engine.compute_diff(current, previous, "target1", "sprint-2", "sprint-1")

        # Same entity (case-insensitive), same finding_id, so nothing new/disappeared/changed
        assert len(result.new_findings) == 0
        assert len(result.disappeared_findings) == 0
        assert len(result.changed_entities) == 0

    def test_diff_returns_sprint_diff_result(self):
        """compute_diff returns a SprintDiffResult dataclass."""
        engine = SprintDiffEngine()
        current = [
            {"ioc_type": "domain", "ioc_value": "test.com", "finding_id": "f1"},
        ]
        result = engine.compute_diff(current, None, "target1", "sprint-1", None)

        assert isinstance(result, SprintDiffResult)
        assert hasattr(result, "target_id")
        assert hasattr(result, "current_sprint_id")
        assert hasattr(result, "previous_sprint_id")
        assert hasattr(result, "new_findings")
        assert hasattr(result, "disappeared_findings")
        assert hasattr(result, "changed_entities")


# ============================================================================
# F203A-6/7/8: TargetProfileSummary build and velocity
# ============================================================================

class TestTargetProfileSummary:
    """F203A-2: TargetProfileSummary build and velocity."""

    def test_profile_first_sprint(self):
        """First sprint: first_seen == last_seen == current_ts."""
        engine = SprintDiffEngine()
        current = [
            {"ioc_type": "domain", "ioc_value": "test.com", "finding_id": "f1"},
        ]
        ts = time.time()
        profile = engine.build_target_profile(current, None, "target1", ts)

        assert profile.first_seen == ts
        assert profile.last_seen == ts
        assert profile.cumulative_finding_count == 1

    def test_profile_velocity_calculation(self):
        """Finding velocity = cumulative / days_since_first_seen."""
        engine = SprintDiffEngine()
        current = [
            {"ioc_type": "domain", "ioc_value": "test.com", "finding_id": "f1"},
        ]
        ts_now = time.time()
        ts_prev = ts_now - (5 * 86400)  # 5 days ago

        prev_profile = TargetProfileSummary(
            target_id="target1",
            first_seen=ts_prev,
            last_seen=ts_prev,
            cumulative_finding_count=10,
            entity_summary_json="{}",
        )
        profile = engine.build_target_profile(current, prev_profile, "target1", ts_now)

        # 10 prev + 1 current = 11 total
        assert profile.cumulative_finding_count == 11
        # velocity = 11 / 5 days ≈ 2.2
        assert profile.finding_velocity == pytest.approx(2.2, rel=0.1)

    def test_profile_entity_summary_json(self):
        """entity_summary_json is valid JSON with entity type breakdown."""
        engine = SprintDiffEngine()
        current = [
            {"ioc_type": "domain", "ioc_value": "a.com", "finding_id": "f1"},
            {"ioc_type": "domain", "ioc_value": "b.com", "finding_id": "f2"},
            {"ioc_type": "ip", "ioc_value": "1.2.3.4", "finding_id": "f3"},
        ]
        profile = engine.build_target_profile(current, None, "target1", time.time())

        summary = json.loads(profile.entity_summary_json)
        assert summary["by_type"]["domain"] == 2
        assert summary["by_type"]["ip"] == 1
        assert summary["total"] == 3

    def test_profile_entity_types_filled(self):
        """build_target_profile populates entity_types dict."""
        engine = SprintDiffEngine()
        current = [
            {"ioc_type": "domain", "ioc_value": "a.com", "finding_id": "f1"},
            {"ioc_type": "ip", "ioc_value": "1.2.3.4", "finding_id": "f2"},
            {"ioc_type": "ip", "ioc_value": "5.6.7.8", "finding_id": "f3"},
        ]
        profile = engine.build_target_profile(current, None, "target1", time.time())

        assert profile.entity_types["domain"] == 1
        assert profile.entity_types["ip"] == 2

    def test_profile_cumulative_count_grows(self):
        """Cumulative count grows across successive sprints."""
        engine = SprintDiffEngine()
        ts1 = time.time() - 86400
        ts2 = time.time()

        profile1 = engine.build_target_profile(
            [{"ioc_type": "domain", "ioc_value": "a.com", "finding_id": "f1"}],
            None,
            "target1",
            ts1,
        )
        profile2 = engine.build_target_profile(
            [{"ioc_type": "domain", "ioc_value": "b.com", "finding_id": "f2"}],
            profile1,
            "target1",
            ts2,
        )
        profile3 = engine.build_target_profile(
            [{"ioc_type": "domain", "ioc_value": "c.com", "finding_id": "f3"}],
            profile2,
            "target1",
            time.time(),
        )

        assert profile1.cumulative_finding_count == 1
        assert profile2.cumulative_finding_count == 2
        assert profile3.cumulative_finding_count == 3

    def test_profile_first_seen_never_increases(self):
        """first_seen is the minimum across all sprints."""
        engine = SprintDiffEngine()
        ts_old = time.time() - 100000
        ts_new = time.time()

        old_profile = TargetProfileSummary(
            target_id="target1",
            first_seen=ts_old,
            last_seen=ts_old,
            cumulative_finding_count=5,
            entity_summary_json="{}",
        )
        new_profile = engine.build_target_profile(
            [{"ioc_type": "domain", "ioc_value": "new.com", "finding_id": "f1"}],
            old_profile,
            "target1",
            ts_new,
        )

        assert new_profile.first_seen == ts_old


# ============================================================================
# F203A-9: Fail-soft behavior
# ============================================================================

class TestSprintDiffEngineFailSoft:
    """F203A-3: Fail-soft behavior.

    The module uses '?' as fallback for missing ioc_type/ioc_value.
    It does NOT skip malformed findings - they produce entity key '?:?'.
    Fail-soft means: exceptions in _entity_key are caught and findings are skipped.
    """

    def test_malformed_finding_with_missing_keys_produces_fallback_key(self):
        """Missing ioc_type/ioc_value uses '?' fallback key (not skipped)."""
        engine = SprintDiffEngine()
        # Finding with missing ioc_type and ioc_value
        finding = {"finding_id": "f1"}
        key = engine._entity_key(finding)
        # Should produce '?::?' not raise
        assert key == "?::?"

    def test_malformed_finding_with_only_ioc_type(self):
        """Finding with only ioc_type produces 'type::?' key."""
        engine = SprintDiffEngine()
        finding = {"ioc_type": "domain"}
        key = engine._entity_key(finding)
        assert key == "domain::?"

    def test_malformed_finding_with_only_ioc_value(self):
        """Finding with only ioc_value produces '?::value' key."""
        engine = SprintDiffEngine()
        finding = {"ioc_value": "test.com"}
        key = engine._entity_key(finding)
        assert key == "?::test.com"

    def test_duplicate_fallback_keys_deduplicated_with_previous(self):
        """Multiple malformed findings with same fallback key are deduplicated."""
        engine = SprintDiffEngine()
        # Use empty previous to force key-based diffing
        current = [
            {"ioc_type": "domain", "ioc_value": "good.com", "finding_id": "f1"},
            {"finding_id": "f2"},  # key = ?::?
            {"finding_id": "f3"},  # key = ?::?
        ]
        result = engine.compute_diff(current, [], "target1", "sprint-2", "sprint-1")

        # With empty previous, all 3 are "new" in terms of diff result
        # But due to key dedup, curr_by_key only keeps last duplicate
        # So we get: domain::good.com + ?::? = 2 unique keys = 2 new findings
        assert len(result.new_findings) == 2

    def test_empty_current_findings_first_sprint(self):
        """First sprint with no findings returns empty diff."""
        engine = SprintDiffEngine()
        result = engine.compute_diff([], None, "target1", "sprint-1", None)

        assert len(result.new_findings) == 0
        assert len(result.disappeared_findings) == 0
        assert len(result.changed_entities) == 0

    def test_empty_previous_findings_all_new(self):
        """Empty previous findings: all current findings are new."""
        engine = SprintDiffEngine()
        current = [
            {"ioc_type": "domain", "ioc_value": "new.com", "finding_id": "f1"},
        ]
        result = engine.compute_diff(current, [], "target1", "sprint-2", "sprint-1")

        assert len(result.new_findings) == 1
        assert len(result.disappeared_findings) == 0


# ============================================================================
# F203A-3/4: Bounds and entity key tests
# ============================================================================

class TestSprintDiffEngineBounds:
    """F203A-3/4: Bounds and entity key logic."""

    def test_max_diff_findings_constant(self):
        """MAX_DIFF_FINDINGS is 100."""
        assert MAX_DIFF_FINDINGS == 100

    def test_max_profile_entries_constant(self):
        """MAX_PROFILE_ENTRIES is 500."""
        assert MAX_PROFILE_ENTRIES == 500

    def test_disappeared_findings_capped(self):
        """disappeared_findings is capped at MAX_DIFF_FINDINGS."""
        engine = SprintDiffEngine()
        previous = [
            {"ioc_type": "domain", "ioc_value": f"old{i}.com", "finding_id": f"f_old{i}"}
            for i in range(150)
        ]
        current = [
            {"ioc_type": "domain", "ioc_value": "new.com", "finding_id": "f_new"},
        ]
        result = engine.compute_diff(current, previous, "target1", "sprint-2", "sprint-1")

        assert len(result.disappeared_findings) == 100  # capped

    def test_changed_entities_capped(self):
        """changed_entities is capped at MAX_DIFF_FINDINGS."""
        engine = SprintDiffEngine()
        previous = [
            {
                "ioc_type": "domain",
                "ioc_value": f"shared{i}.com",
                "finding_id": f"old_f{i}",
            }
            for i in range(150)
        ]
        current = [
            {
                "ioc_type": "domain",
                "ioc_value": f"shared{i}.com",
                "finding_id": f"new_f{i}",
            }
            for i in range(150)
        ]
        result = engine.compute_diff(current, previous, "target1", "sprint-2", "sprint-1")

        assert len(result.changed_entities) == 100  # capped

    def test_entity_key_missing_ioc_type_uses_question_mark(self):
        """Missing ioc_type defaults to '?' in entity key."""
        engine = SprintDiffEngine()
        finding = {"ioc_value": "test.com"}  # no ioc_type
        key = engine._entity_key(finding)
        assert key.startswith("?")

    def test_entity_key_missing_ioc_value_uses_question_mark(self):
        """Missing ioc_value defaults to '?' in entity key."""
        engine = SprintDiffEngine()
        finding = {"ioc_type": "domain"}  # no ioc_value
        key = engine._entity_key(finding)
        assert "::" in key
        assert key.endswith("?")


# ============================================================================
# Smoke tests
# ============================================================================

def test_sprint_diff_engine_module_imports():
    """Module can be imported without error."""
    from hledac.universal.knowledge import sprint_diff_engine

    assert sprint_diff_engine is not None
    assert hasattr(sprint_diff_engine, "SprintDiffEngine")
    assert hasattr(sprint_diff_engine, "SprintDiffResult")
    assert hasattr(sprint_diff_engine, "TargetProfileSummary")


def test_factory_creates_engine():
    """SprintDiffEngine can be instantiated."""
    engine = SprintDiffEngine()
    assert isinstance(engine, SprintDiffEngine)
    assert hasattr(engine, "compute_diff")
    assert hasattr(engine, "build_target_profile")
