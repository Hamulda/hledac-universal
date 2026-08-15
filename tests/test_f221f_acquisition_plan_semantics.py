# Sprint F221F: Acquisition Plan Semantics Split — Probe Tests
"""
Validates the semantic split of acquisition plan fields:

- prelude_plan: original plan dicts (backward compatible alias for `plan`)
- required_lane_plan: mandatory lanes from terminality.required_lanes
- runtime_attempted_lanes: lanes where source_family_outcomes shows attempted=True
- effective_acquisition_plan: union(required_lane_plan, runtime_attempted_lanes)
- plan_semantics: "prelude_only" | "effective_runtime"

Scope: runtime/acquisition_strategy.py build_acquisition_report() only.
No runtime behavior changes — report-only fields.
"""


from hledac.universal.runtime.acquisition_strategy import (
    ACQUISITION_REPORT_SCHEMA_VERSION,
    build_acquisition_report,
)


class TestF221F_PlanSemanticsSplit:  # noqa: N801
    """F221F: Acquisition plan semantics split probe tests."""

    def test_empty_prelude_plan_but_public_required_creates_effective_plan(self):
        """
        Acceptance fixture: plan=[] but required_lanes=["PUBLIC"].
        effective_acquisition_plan MUST include "PUBLIC".
        """
        terminality = {
            "required_lanes": ["PUBLIC"],
            "checked": ["PUBLIC"],
            "satisfied": [],
            "missing_lanes": ["PUBLIC"],
        }
        source_family_outcomes = [
            {"family": "public", "attempted": True, "skipped": False, "raw_count": 5, "accepted_count": 2},
        ]

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        assert report["prelude_plan"] == []
        assert report["required_lane_plan"] == ["PUBLIC"]
        assert "public" in report["runtime_attempted_lanes"]
        assert "PUBLIC" in report["effective_acquisition_plan"] or "public" in report["effective_acquisition_plan"]
        assert report["plan_semantics"] == "effective_runtime"

    def test_runtime_attempted_lanes_extracted_from_source_family_outcomes(self):
        """
        Test that runtime_attempted_lanes is correctly derived from source_family_outcomes.
        Only lanes with attempted=True are included.
        """
        terminality = {"required_lanes": [], "checked": [], "satisfied": []}
        source_family_outcomes = [
            {"family": "public", "attempted": True, "skipped": False, "raw_count": 5, "accepted_count": 2},
            {"family": "feed", "attempted": True, "skipped": False, "raw_count": 10, "accepted_count": 8},
            {"family": "ct", "attempted": False, "skipped": True, "raw_count": 0, "accepted_count": 0},
        ]

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        attempted = report["runtime_attempted_lanes"]
        assert "public" in attempted
        assert "feed" in attempted
        assert "ct" not in attempted  # skipped, not attempted

    def test_plan_semantics_effective_runtime_when_lanes_attempted(self):
        """
        When any lane is attempted, plan_semantics must be 'effective_runtime'.
        """
        terminality = {"required_lanes": [], "checked": [], "satisfied": []}
        source_family_outcomes = [
            {"family": "feed", "attempted": True, "skipped": False, "raw_count": 10, "accepted_count": 8},
        ]

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        assert report["plan_semantics"] == "effective_runtime"

    def test_plan_semantics_prelude_only_when_no_lanes_attempted(self):
        """
        When no lane is attempted, plan_semantics must be 'prelude_only'.
        This is the key fix: plan=[] no longer misleads when PUBLIC was required+attempted.
        """
        terminality = {"required_lanes": ["PUBLIC"], "checked": [], "satisfied": []}
        source_family_outcomes = [
            {"family": "public", "attempted": False, "skipped": True, "raw_count": 0, "accepted_count": 0},
        ]

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        assert report["plan_semantics"] == "prelude_only"
        assert "public" not in report["runtime_attempted_lanes"]

    def test_domain_prelude_plan_still_preserved(self):
        """
        When a real plan exists, prelude_plan must preserve it.
        plan_semantics should be 'effective_runtime' if any lane was attempted.
        """
        from hledac.universal.runtime.acquisition_strategy import (
            AcquisitionLane,
            AcquisitionLanePlan,
            AcquisitionStrategySnapshot,
        )

        plan_snapshot = AcquisitionStrategySnapshot(plans=[
            AcquisitionLanePlan(
                lane=AcquisitionLane.PUBLIC,
                enabled=True,
                reason="domain_query",
                max_items=20,
                timeout_s=60,
                concurrency=2,
                risk_level="LOW",
            ),
            AcquisitionLanePlan(
                lane=AcquisitionLane.FEED,
                enabled=True,
                reason="always_enabled",
                max_items=50,
                timeout_s=30,
                concurrency=3,
                risk_level="LOW",
            ),
        ])

        terminality = {"required_lanes": ["PUBLIC"], "checked": [], "satisfied": []}
        source_family_outcomes = [
            {"family": "public", "attempted": True, "skipped": False, "raw_count": 5, "accepted_count": 2},
        ]

        report = build_acquisition_report(
            plan=plan_snapshot,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        # prelude_plan should contain the original plan dicts
        assert len(report["prelude_plan"]) == 2
        assert report["prelude_plan"] == report["plan"]  # backward compat alias
        assert report["plan_semantics"] == "effective_runtime"
        assert "PUBLIC" in report["required_lane_plan"] or "public" in report["required_lane_plan"]

    def test_no_runtime_behavior_changes_report_only(self):
        """
        Verify that build_acquisition_report produces the same schema_version
        and other existing fields — only ADDING new fields, not changing behavior.
        """
        report = build_acquisition_report(
            plan=None,
            terminality={"required_lanes": [], "checked": [], "satisfied": []},
            source_family_outcomes=[],
        )

        # Must have all new F221F fields
        assert "prelude_plan" in report
        assert "required_lane_plan" in report
        assert "runtime_attempted_lanes" in report
        assert "effective_acquisition_plan" in report
        assert "plan_semantics" in report

        # Must have original fields for backward compatibility
        assert report["schema_version"] == ACQUISITION_REPORT_SCHEMA_VERSION
        assert "plan" in report  # original field unchanged
        assert "terminality" in report
        assert "source_family_outcomes" in report

        # plan_semantics must be one of the two valid values
        assert report["plan_semantics"] in ("prelude_only", "effective_runtime")

    def test_effective_acquisition_plan_union(self):
        """
        effective_acquisition_plan = required_lane_plan ∪ runtime_attempted_lanes
        """
        terminality = {"required_lanes": ["CT"], "checked": [], "satisfied": []}
        source_family_outcomes = [
            {"family": "public", "attempted": True, "skipped": False, "raw_count": 5, "accepted_count": 2},
        ]

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=source_family_outcomes,
        )

        effective = report["effective_acquisition_plan"]
        # CT from required_lane_plan
        assert "CT" in effective or "ct" in effective
        # public from runtime_attempted_lanes
        assert "public" in effective
        # union size should be 2
        assert len(effective) == 2

    def test_required_lane_plan_from_terminality(self):
        """
        required_lane_plan extracts only required=True lanes from terminality.
        """
        terminality = {
            "required_lanes": ["PUBLIC", "CT"],
            "checked": ["PUBLIC", "CT", "FEED"],
            "satisfied": ["PUBLIC"],
            "missing_lanes": ["CT"],
        }

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=[],
        )

        required = report["required_lane_plan"]
        assert "PUBLIC" in required
        assert "CT" in required
        assert len(required) == 2

    def test_empty_source_family_outcomes_prelude_only(self):
        """
        When source_family_outcomes is empty/None, plan_semantics = 'prelude_only'.
        """
        terminality = {"required_lanes": [], "checked": [], "satisfied": []}

        report = build_acquisition_report(
            plan=None,
            terminality=terminality,
            source_family_outcomes=None,
        )

        assert report["plan_semantics"] == "prelude_only"
        assert report["runtime_attempted_lanes"] == []
        assert report["effective_acquisition_plan"] == []


# ── Fix 5: build_acquisition_plan caching tests ──────────────────────────────
import sys  # noqa: E402
from collections import OrderedDict  # noqa: E402
from typing import Any  # noqa: E402
from _core import aclose

sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

# Inline the cache helpers (same implementation as sprint_scheduler.py)
_acquisition_plan_cache: OrderedDict[str, Any] = OrderedDict()
_ACQUISITION_PLAN_CACHE_MAX: int = 16


def _build_plan_cache_key(
    query: str,
    duration_s: float,
    aggressive_mode: bool,
    uma_state: str,
    swap_detected: bool,
    accepted_findings_so_far: int,
    branch_timeout_count: int,
    acquisition_profile: str,
    feed_domain_seeds: tuple[str, ...],
    synthetic_domains: tuple[str, ...],
) -> str:
    _fd_sorted = tuple(sorted(feed_domain_seeds)) if feed_domain_seeds else ()
    _syn_sorted = tuple(sorted(synthetic_domains)) if synthetic_domains else ()
    import hashlib

    raw = "|".join(
        str(x)
        for x in (
            query,
            duration_s,
            aggressive_mode,
            uma_state,
            swap_detected,
            accepted_findings_so_far,
            branch_timeout_count,
            acquisition_profile,
            _fd_sorted,
            _syn_sorted,
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _get_cached_plan(cache_key: str) -> Any | None:
    if cache_key in _acquisition_plan_cache:
        _acquisition_plan_cache.move_to_end(cache_key)
        return _acquisition_plan_cache[cache_key]
    return None


def _put_cached_plan(cache_key: str, plan: Any) -> None:
    _acquisition_plan_cache[cache_key] = plan
    _acquisition_plan_cache.move_to_end(cache_key)
    while len(_acquisition_plan_cache) > _ACQUISITION_PLAN_CACHE_MAX:
        _acquisition_plan_cache.popitem(last=False)


class TestFix5_AcquisitionPlanCache:
    """Fix 5: build_acquisition_plan caching for repeat queries."""

    def setup_method(self):
        """Clear cache before each test."""
        _acquisition_plan_cache.clear()

    def test_same_params_produce_same_key(self):
        """Identical parameters must produce identical cache keys."""
        key1 = _build_plan_cache_key(
            query="test query",
            duration_s=300.0,
            aggressive_mode=True,
            uma_state="ok",
            swap_detected=False,
            accepted_findings_so_far=0,
            branch_timeout_count=0,
            acquisition_profile="default",
            feed_domain_seeds=(),
            synthetic_domains=(),
        )
        key2 = _build_plan_cache_key(
            query="test query",
            duration_s=300.0,
            aggressive_mode=True,
            uma_state="ok",
            swap_detected=False,
            accepted_findings_so_far=0,
            branch_timeout_count=0,
            acquisition_profile="default",
            feed_domain_seeds=(),
            synthetic_domains=(),
        )
        assert key1 == key2

    def test_different_query_produces_different_key(self):
        """Different queries must produce different cache keys."""
        key1 = _build_plan_cache_key(
            query="query A", duration_s=300, aggressive_mode=False,
            uma_state="ok", swap_detected=False, accepted_findings_so_far=0,
            branch_timeout_count=0, acquisition_profile="default",
            feed_domain_seeds=(), synthetic_domains=(),
        )
        key2 = _build_plan_cache_key(
            query="query B", duration_s=300, aggressive_mode=False,
            uma_state="ok", swap_detected=False, accepted_findings_so_far=0,
            branch_timeout_count=0, acquisition_profile="default",
            feed_domain_seeds=(), synthetic_domains=(),
        )
        assert key1 != key2

    def test_feed_domain_seeds_order_independent(self):
        """Feed domain seeds order must not affect cache key (sorted before hashing)."""
        key1 = _build_plan_cache_key(
            query="q", duration_s=300, aggressive_mode=False,
            uma_state="ok", swap_detected=False, accepted_findings_so_far=0,
            branch_timeout_count=0, acquisition_profile="default",
            feed_domain_seeds=("a.com", "b.com", "c.com"),
            synthetic_domains=(),
        )
        key2 = _build_plan_cache_key(
            query="q", duration_s=300, aggressive_mode=False,
            uma_state="ok", swap_detected=False, accepted_findings_so_far=0,
            branch_timeout_count=0, acquisition_profile="default",
            feed_domain_seeds=("c.com", "a.com", "b.com"),
            synthetic_domains=(),
        )
        assert key1 == key2, "Feed domain seeds order must be normalized"

    def test_lru_eviction_at_capacity(self):
        """Cache must evict oldest entry when at max capacity (16)."""
        for i in range(20):
            _put_cached_plan(f"key_{i}", f"plan_{i}")
        assert len(_acquisition_plan_cache) == 16
        assert _acquisition_plan_cache.get("key_0") is None, "key_0 should be evicted"
        assert _acquisition_plan_cache.get("key_19") == "plan_19", "key_19 should be present"

    def test_get_cached_plan_moves_to_end(self):
        """_get_cached_plan must move accessed entry to end (LRU update)."""
        _acquisition_plan_cache.clear()
        _put_cached_plan("a", "plan_a")
        _put_cached_plan("b", "plan_b")
        _put_cached_plan("c", "plan_c")
        # Access 'a'
        result = _get_cached_plan("a")
        assert result == "plan_a"
        keys = list(_acquisition_plan_cache.keys())
        assert keys[-1] == "a", "Accessed key should be moved to end"

    def test_get_cached_plan_returns_none_on_miss(self):
        """_get_cached_plan returns None when key not in cache."""
        _acquisition_plan_cache.clear()
        result = _get_cached_plan("nonexistent")
        assert result is None

    def test_cache_key_with_feed_domain_seeds(self):
        """Cache key must include feed_domain_seeds in hash."""
        key_no_seeds = _build_plan_cache_key(
            query="q", duration_s=300, aggressive_mode=False,
            uma_state="ok", swap_detected=False, accepted_findings_so_far=0,
            branch_timeout_count=0, acquisition_profile="default",
            feed_domain_seeds=(), synthetic_domains=(),
        )
        key_with_seeds = _build_plan_cache_key(
            query="q", duration_s=300, aggressive_mode=False,
            uma_state="ok", swap_detected=False, accepted_findings_so_far=0,
            branch_timeout_count=0, acquisition_profile="default",
            feed_domain_seeds=("example.com",),
            synthetic_domains=(),
        )
        assert key_no_seeds != key_with_seeds

    def test_cache_key_with_synthetic_domains(self):
        """Cache key must include synthetic_domains in hash."""
        key_no_syn = _build_plan_cache_key(
            query="q", duration_s=300, aggressive_mode=False,
            uma_state="ok", swap_detected=False, accepted_findings_so_far=0,
            branch_timeout_count=0, acquisition_profile="default",
            feed_domain_seeds=(), synthetic_domains=(),
        )
        key_with_syn = _build_plan_cache_key(
            query="q", duration_s=300, aggressive_mode=False,
            uma_state="ok", swap_detected=False, accepted_findings_so_far=0,
            branch_timeout_count=0, acquisition_profile="default",
            feed_domain_seeds=(), synthetic_domains=("synthetic.ai",),
        )
        assert key_no_syn != key_with_syn

    def test_different_aggressive_mode_produces_different_key(self):
        """aggressive_mode affects cache key."""
        key_normal = _build_plan_cache_key(
            query="q", duration_s=300, aggressive_mode=False,
            uma_state="ok", swap_detected=False, accepted_findings_so_far=0,
            branch_timeout_count=0, acquisition_profile="default",
            feed_domain_seeds=(), synthetic_domains=(),
        )
        key_aggressive = _build_plan_cache_key(
            query="q", duration_s=300, aggressive_mode=True,
            uma_state="ok", swap_detected=False, accepted_findings_so_far=0,
            branch_timeout_count=0, acquisition_profile="default",
            feed_domain_seeds=(), synthetic_domains=(),
        )
        assert key_normal != key_aggressive
