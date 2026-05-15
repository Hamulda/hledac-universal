"""
F220B: Pivot Lane Planner — probe tests

Tests:
1. domain schedules doh/ct/wayback
2. url schedules wayback/public
3. ip schedules bgp/passive_dns
4. entity schedules public_rescue
5. flags disable lanes
6. max_items bound
7. dedupe
8. no heavy imports
"""

import sys
from pathlib import Path

# Ensure the package root is on the path (same pattern as existing probe tests)
sys.path.insert(0, "hledac/universal")

from dataclasses import dataclass as _dataclass
from pipeline.pivot_lane_planner import (
    plan_lanes_for_pivot_seeds,
    PivotLanePlan,
    LanePlanItem,
)


# ----------------------------------------------------------------------
# Test helpers
# ----------------------------------------------------------------------


@_dataclass(frozen=True, slots=True)
class _MockPivotSeed:
    value: str
    seed_type: str
    source_family: str = "test"
    confidence: float = 0.9
    reason: str = "test"


def _seeds(*types_and_values) -> list[_MockPivotSeed]:
    """Make PivotSeed mocks from alternating type, value pairs."""
    seeds = []
    for i in range(0, len(types_and_values), 2):
        seed_type = types_and_values[i]
        value = types_and_values[i + 1]
        seeds.append(_MockPivotSeed(value=value, seed_type=seed_type))
    return seeds


def _lane_names(plan: PivotLanePlan) -> set[str]:
    return {item.lane for item in plan.items}


def _seed_values_by_lane(plan: PivotLanePlan, lane: str) -> list[str]:
    return [item.seed_value for item in plan.items if item.lane == lane]


# ----------------------------------------------------------------------
# Test 1: domain → DOH + CT + WAYBACK + PASSIVE_DNS
# ----------------------------------------------------------------------


def test_domain_schedules_doh_ct_wayback_passive_dns():
    seeds = _seeds("domain", "evil.com")
    plan = plan_lanes_for_pivot_seeds(seeds)

    lanes = _lane_names(plan)
    assert "DOH" in lanes, f"DOH missing from {lanes}"
    assert "CT" in lanes, f"CT missing from {lanes}"
    assert "WAYBACK" in lanes, f"WAYBACK missing from {lanes}"
    assert "PASSIVE_DNS" in lanes, f"PASSIVE_DNS missing from {lanes}"
    assert "BGP" not in lanes, "BGP should not be planned for domain"
    assert "PUBLIC" not in lanes, "PUBLIC should not be planned for domain"
    assert plan.skipped == (), f"domain should not be skipped: {plan.skipped}"
    print("  test_domain_schedules_doh_ct_wayback_passive_dns PASS")


# ----------------------------------------------------------------------
# Test 2: url → WAYBACK + PUBLIC
# ----------------------------------------------------------------------


def test_url_schedules_wayback_and_public():
    seeds = _seeds("url", "https://evil.com/payload")
    plan = plan_lanes_for_pivot_seeds(seeds)

    lanes = _lane_names(plan)
    assert "WAYBACK" in lanes, f"WAYBACK missing from {lanes}"
    assert "PUBLIC" in lanes, f"PUBLIC missing from {lanes}"
    assert "DOH" not in lanes, f"DOH should not be planned for url: {lanes}"
    assert "CT" not in lanes, f"CT should not be planned for url: {lanes}"
    assert "BGP" not in lanes, f"BGP should not be planned for url: {lanes}"
    print("  test_url_schedules_wayback_and_public PASS")


# ----------------------------------------------------------------------
# Test 3: ip → BGP + PASSIVE_DNS (+ DOH reverse)
# ----------------------------------------------------------------------


def test_ip_schedules_bgp_passive_dns():
    seeds = _seeds("ip", "1.2.3.4")
    plan = plan_lanes_for_pivot_seeds(seeds)

    lanes = _lane_names(plan)
    assert "BGP" in lanes, f"BGP missing from {lanes}"
    assert "PASSIVE_DNS" in lanes, f"PASSIVE_DNS missing from {lanes}"
    assert "DOH" in lanes, f"DOH reverse missing from {lanes}"
    print("  test_ip_schedules_bgp_passive_dns PASS")


# ----------------------------------------------------------------------
# Test 4: entity → PUBLIC only
# ----------------------------------------------------------------------


def test_entity_schedules_public_rescue():
    seeds = _seeds("entity", "Ghost Threat Actor")
    plan = plan_lanes_for_pivot_seeds(seeds)

    lanes = _lane_names(plan)
    assert "PUBLIC" in lanes, f"PUBLIC missing from {lanes}"
    assert len(plan.items) == 1, f"entity should produce exactly 1 item: {len(plan.items)}"
    assert plan.items[0].reason == "entity_public_rescue"
    print("  test_entity_schedules_public_rescue PASS")


# ----------------------------------------------------------------------
# Test 5: flags disable lanes
# ----------------------------------------------------------------------


def test_flags_disable_lanes():
    seeds = _seeds("domain", "test.com")
    plan = plan_lanes_for_pivot_seeds(
        seeds,
        enable_doh=False,
        enable_ct=False,
        enable_wayback=False,
        enable_passive_dns=False,
        enable_bgp=False,
    )

    lanes = _lane_names(plan)
    assert len(lanes) == 0, f"All lanes disabled but got: {lanes}"
    assert plan.skipped == (), f"Nothing should be skipped: {plan.skipped}"
    print("  test_flags_disable_lanes PASS")


def test_ct_disabled_only():
    seeds = _seeds("domain", "test.com")
    plan = plan_lanes_for_pivot_seeds(seeds, enable_ct=False)

    lanes = _lane_names(plan)
    assert "CT" not in lanes, f"CT should be disabled: {lanes}"
    assert "DOH" in lanes, "DOH should still be enabled"
    print("  test_ct_disabled_only PASS")


# ----------------------------------------------------------------------
# Test 6: max_items bound
# ----------------------------------------------------------------------


def test_max_items_bound():
    # Generate many seeds
    seeds = _seeds(
        "domain", "d1.com",
        "domain", "d2.com",
        "domain", "d3.com",
        "domain", "d4.com",
        "domain", "d5.com",
    )
    # 5 domains × 4 lanes = 20 items, limit to 3
    plan = plan_lanes_for_pivot_seeds(seeds, max_items=3)

    assert len(plan.items) == 3, f"max_items=3 but got {len(plan.items)}"
    print("  test_max_items_bound PASS")


def test_max_items_exact():
    seeds = _seeds(
        "domain", "d1.com",
        "domain", "d2.com",
    )
    # 2 domains × 4 lanes = 8 items, limit to 8
    plan = plan_lanes_for_pivot_seeds(seeds, max_items=8)

    assert len(plan.items) == 8, f"max_items=8 but got {len(plan.items)}"
    print("  test_max_items_exact PASS")


# ----------------------------------------------------------------------
# Test 7: dedupe
# ----------------------------------------------------------------------


def test_dedupe_same_seed_twice():
    # Same domain passed twice
    seeds = [
        _MockPivotSeed(value="evil.com", seed_type="domain"),
        _MockPivotSeed(value="evil.com", seed_type="domain"),
    ]
    plan = plan_lanes_for_pivot_seeds(seeds)

    # Should NOT produce duplicate DOH entries for same value
    doh_count = sum(1 for item in plan.items if item.lane == "DOH")
    assert doh_count == 1, f"DOH should appear once, got {doh_count}"

    # Total items should still include all lanes for 1 unique domain
    assert len(plan.items) == 4, f"4 lanes for 1 domain, got {len(plan.items)}"
    print("  test_dedupe_same_seed_twice PASS")


def test_dedupe_different_domains():
    seeds = _seeds("domain", "a.com", "domain", "b.com")
    plan = plan_lanes_for_pivot_seeds(seeds)

    assert len(plan.items) == 8, f"2 domains × 4 lanes = 8, got {len(plan.items)}"
    print("  test_dedupe_different_domains PASS")


# ----------------------------------------------------------------------
# Test 8: no heavy imports (must be importable without network/fs deps)
# ----------------------------------------------------------------------


def test_no_heavy_imports():
    import importlib
    import pipeline.pivot_lane_planner as m

    # Should import without hitting network, LMDB, MLX, etc.
    importlib.reload(m)

    # Quick sanity check the function exists
    assert hasattr(m, "plan_lanes_for_pivot_seeds")
    assert hasattr(m, "PivotLanePlan")
    assert hasattr(m, "LanePlanItem")
    print("  test_no_heavy_imports PASS")


# ----------------------------------------------------------------------
# Test 9: empty seeds
# ----------------------------------------------------------------------


def test_empty_seeds():
    plan = plan_lanes_for_pivot_seeds([])
    assert plan.items == ()
    assert plan.skipped == ()
    assert plan.reason == "no_seeds"
    print("  test_empty_seeds PASS")


# ----------------------------------------------------------------------
# Test 10: hash type → skipped
# ----------------------------------------------------------------------


def test_hash_skipped():
    seeds = _seeds("hash", "deadbeef12345678deadbeef12345678", "md5", "abcd1234abcd1234abcd1234abcd1234")
    plan = plan_lanes_for_pivot_seeds(seeds)

    assert len(plan.items) == 0, f"hash should produce no items: {len(plan.items)}"
    assert len(plan.skipped) == 2, f"2 hashes should be skipped: {plan.skipped}"
    print("  test_hash_skipped PASS")


# ----------------------------------------------------------------------
# Test 11: priorities are positive floats
# ----------------------------------------------------------------------


def test_priorities_are_valid():
    seeds = _seeds("domain", "test.com")
    plan = plan_lanes_for_pivot_seeds(seeds)

    for item in plan.items:
        assert isinstance(item.priority, float), f"priority not float: {type(item.priority)}"
        assert 0.0 < item.priority <= 1.0, f"priority out of range: {item.priority}"
    print("  test_priorities_are_valid PASS")


# ----------------------------------------------------------------------
# Run all
# ----------------------------------------------------------------------


if __name__ == "__main__":
    tests = [
        test_domain_schedules_doh_ct_wayback_passive_dns,
        test_url_schedules_wayback_and_public,
        test_ip_schedules_bgp_passive_dns,
        test_entity_schedules_public_rescue,
        test_flags_disable_lanes,
        test_ct_disabled_only,
        test_max_items_bound,
        test_max_items_exact,
        test_dedupe_same_seed_twice,
        test_dedupe_different_domains,
        test_no_heavy_imports,
        test_empty_seeds,
        test_hash_skipped,
        test_priorities_are_valid,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  {t.__name__} FAIL: {exc}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"RESULTS: {passed} passed, {failed} failed")
    if failed == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"{failed} TESTS FAILED")
        sys.exit(1)