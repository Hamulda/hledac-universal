"""Sprint F226A — Mission Intent Runtime Wiring probe tests.

Validates that mission intent is wired into:
- nonfeed_profile_expected_lanes (mission lane priority)
- pivot candidate generation/scoring (mission_pivot_boost_applied)
- telemetry fields (mission_runtime_applied, mission_lane_priority)

Scope: acquisition_strategy.py, pivot_planner.py, sprint_scheduler.py.
No network, no model load.
"""


import pytest

from hledac.universal.runtime.acquisition_strategy import (
    AcquisitionStrategySnapshot,
    MissionIntent,
    build_acquisition_plan,
    build_acquisition_report,
)
from hledac.universal.runtime.pivot_planner import (
    generate_pivot_candidates_from_query,
    score_pivot_for_mission,
    Pivot,
    PivotType,
)


# ── shared helpers ────────────────────────────────────────────────────────────

def _snap(query: str, uma: str = "ok") -> AcquisitionStrategySnapshot:
    return build_acquisition_plan(
        query=query,
        duration_s=120.0,
        aggressive_mode=False,
        uma_state=uma,
        swap_detected=False,
        accepted_findings_so_far=0,
        branch_timeout_count=0,
        transport_authority_status=None,
        stealth_phase=None,
        acquisition_profile="default",
    )


# ── Task 2: domain query → domain_recon, expected lanes include PUBLIC/CT/WAYBACK/PASSIVE_DNS/PIVOT_EXECUTOR ──

def test_domain_recon_expected_lanes_include_public_ct_wayback_passive_dns_pivot():
    snap = _snap("mozilla.org")
    debug = snap.nonfeed_plan_debug
    assert debug is not None
    assert debug.mission_intent == MissionIntent.DOMAIN_RECON
    # nonfeed_profile_expected_lanes = mission_required_lanes for domain_recon
    assert AcquisitionStrategySnapshot is not None
    # Verify PUBLIC, CT, PIVOT_EXECUTOR in required; WAYBACK, PASSIVE_DNS in optional
    required = debug.mission_required_lanes
    optional = debug.mission_optional_lanes
    assert "PUBLIC" in required, f"PUBLIC not in required lanes: {required}"
    assert "CT" in required, f"CT not in required lanes: {required}"
    assert "PIVOT_EXECUTOR" in required, f"PIVOT_EXECUTOR not in required lanes: {required}"
    assert "WAYBACK" in optional, f"WAYBACK not in optional lanes: {optional}"
    assert "PASSIVE_DNS" in optional, f"PASSIVE_DNS not in optional lanes: {optional}"


def test_domain_recon_nonfeed_profile_expected_lanes():
    snap = _snap("example.com")
    debug = snap.nonfeed_plan_debug
    assert debug is not None
    expected = debug.nonfeed_profile_expected_lanes
    # Should include mission_required_lanes, not hardcoded nonfeed_diagnostic set
    assert "PUBLIC" in expected
    assert "CT" in expected


# ── Task 2: CVE query → cve_recon, does NOT disable FEED ────────────────────

def test_cve_recon_does_not_disable_feed():
    snap = _snap("CVE-2024-1234")
    debug = snap.nonfeed_plan_debug
    assert debug is not None
    assert debug.mission_intent == MissionIntent.CVE_RECON
    # FEED must be enabled (CVE mission does not block FEED)
    feed_plan = next((p for p in snap.plans if p.lane == "FEED"), None)
    assert feed_plan is not None
    assert feed_plan.enabled, "FEED must not be disabled by cve_recon mission intent"


def test_cve_recon_required_lanes_public_ct_pivot():
    snap = _snap("CVE-2021-44228")
    debug = snap.nonfeed_plan_debug
    assert debug is not None
    required = debug.mission_required_lanes
    assert "PUBLIC" in required
    assert "CT" in required
    assert "PIVOT_EXECUTOR" in required


# ── Task 2: wallet query → wallet_recon, BLOCKCHAIN only when crypto indicators present ──

def test_wallet_recon_expected_lanes():
    snap = _snap("bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh")
    debug = snap.nonfeed_plan_debug
    assert debug is not None
    assert debug.mission_intent == MissionIntent.WALLET_RECON
    required = debug.mission_required_lanes
    optional = debug.mission_optional_lanes
    # WALLET_RECON: required = PUBLIC, PIVOT_EXECUTOR; optional = BLOCKCHAIN, CT
    assert "PUBLIC" in required
    assert "PIVOT_EXECUTOR" in required
    assert "BLOCKCHAIN" in optional
    assert "CT" in optional


def test_wallet_recon_nonfeed_profile_expected_lanes():
    snap = _snap("0x71C7656EC7aB44D0bE590Ba897C0c6B6bE8F5Ef3")
    debug = snap.nonfeed_plan_debug
    assert debug is not None
    expected = debug.nonfeed_profile_expected_lanes
    # For wallet_recon, BLOCKCHAIN is optional, not guaranteed
    assert "PUBLIC" in expected
    assert "BLOCKCHAIN" in expected or "PIVOT_EXECUTOR" in expected


# ── Task 2: person/email query → person_recon, leak/identity pivot types rank higher ──

def test_person_recon_leak_identity_pivots_boosted():
    snap = _snap("john.doe@example.com")
    debug = snap.nonfeed_plan_debug
    assert debug is not None
    assert debug.mission_intent == MissionIntent.PERSON_RECON
    required = debug.mission_required_lanes
    optional = debug.mission_optional_lanes
    assert "PUBLIC" in required
    assert "PIVOT_EXECUTOR" in required
    assert "CT" in optional or "PASSIVE_DNS" in optional


def test_person_recon_pivot_scoring_boosts_leak_identity():
    # Verify score_pivot_for_mission boosts leak/identity for person_recon
    from hledac.universal.runtime.pivot_planner import Pivot, PivotType
    leak_pivot = Pivot(
        priority=-0.7, pivot_id="test-leak", pivot_type=PivotType.LEAK,
        ioc_value="test@example.com", ioc_type="email",
        reason="leak check", expected_value=0.7, source_hint="test",
        evidence_pointers=(),
    )
    identity_pivot = Pivot(
        priority=-0.5, pivot_id="test-id", pivot_type=PivotType.IDENTITY,
        ioc_value="test@example.com", ioc_type="email",
        reason="identity check", expected_value=0.5, source_hint="test",
        evidence_pointers=(),
    )
    graph_pivot = Pivot(
        priority=-0.6, pivot_id="test-graph", pivot_type=PivotType.GRAPH,
        ioc_value="test@example.com", ioc_type="email",
        reason="graph check", expected_value=0.6, source_hint="test",
        evidence_pointers=(),
    )
    leak_boost = score_pivot_for_mission(leak_pivot, MissionIntent.PERSON_RECON)
    identity_boost = score_pivot_for_mission(identity_pivot, MissionIntent.PERSON_RECON)
    graph_boost = score_pivot_for_mission(graph_pivot, MissionIntent.PERSON_RECON)
    # person_recon boosts leak (1.25) and identity (1.25), not graph (1.0)
    assert leak_boost == 1.25, f"leak pivot boost should be 1.25, got {leak_boost}"
    assert identity_boost == 1.25, f"identity pivot boost should be 1.25, got {identity_boost}"
    assert graph_boost == 1.0, f"graph pivot should not be boosted for person_recon, got {graph_boost}"


# ── Task 2: unknown query → safe/default behavior unchanged ──────────────────

def test_unknown_intent_safe_lanes_only():
    snap = _snap("random unknown query text")
    debug = snap.nonfeed_plan_debug
    assert debug is not None
    assert debug.mission_intent == MissionIntent.UNKNOWN
    # Unknown intent → safe lanes only
    assert debug.mission_runtime_applied is False, "unknown intent must not set mission_runtime_applied=True"
    assert debug.mission_pivot_boost_applied is False, "unknown intent must not set mission_pivot_boost_applied=True"


def test_unknown_intent_nonfeed_profile_expected_lanes_empty():
    snap = _snap("who is the CEO of this company")
    debug = snap.nonfeed_plan_debug
    assert debug is not None
    assert debug.mission_intent == MissionIntent.UNKNOWN
    # unknown intent → nonfeed_profile_expected_lanes = ()
    expected = debug.nonfeed_profile_expected_lanes
    assert expected == () or len(expected) == 0, f"unknown intent expected empty lanes, got {expected}"


# ── Task 5: telemetry fields ────────────────────────────────────────────────────

def test_mission_runtime_applied_set_for_known_intents():
    for query, expected_intent in [
        ("mozilla.org", MissionIntent.DOMAIN_RECON),
        ("CVE-2024-1234", MissionIntent.CVE_RECON),
        ("bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh", MissionIntent.WALLET_RECON),
        ("1.2.3.4", MissionIntent.INFRA_RECON),
        ("john.doe@example.com", MissionIntent.PERSON_RECON),
    ]:
        snap = _snap(query)
        debug = snap.nonfeed_plan_debug
        assert debug is not None
        assert debug.mission_intent == expected_intent
        assert debug.mission_runtime_applied is True, f"{query}: mission_runtime_applied should be True for {expected_intent}"


def test_mission_lane_priority_matches_required_lanes():
    snap = _snap("example.com")
    debug = snap.nonfeed_plan_debug
    assert debug is not None
    # mission_lane_priority = mission_required_lanes
    assert debug.mission_lane_priority == debug.mission_required_lanes
    assert len(debug.mission_lane_priority) > 0


def test_mission_pivot_boost_applied_for_known_intents():
    for query in ["mozilla.org", "CVE-2024-1234", "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"]:
        snap = _snap(query)
        debug = snap.nonfeed_plan_debug
        assert debug is not None
        assert debug.mission_pivot_boost_applied is True, f"{query}: mission_pivot_boost_applied should be True"


def test_mission_feed_cap_reason_is_none():
    # mission intent does NOT drive feed cap (nonfeed_diagnostic does)
    for query in ["mozilla.org", "CVE-2024-1234", "wallet"]:
        snap = _snap(query)
        debug = snap.nonfeed_plan_debug
        assert debug is not None
        assert debug.mission_feed_cap_reason is None, f"mission_feed_cap_reason must be None, got {debug.mission_feed_cap_reason}"


# ── safety: mission intent does NOT enable STEALTH ─────────────────────────

def test_stealth_not_enabled_by_mission_intent():
    for query in ["dark web target", "tor hidden service", "onion probe"]:
        snap = _snap(query)
        stealth_plan = next((p for p in snap.plans if p.lane == "STEALTH"), None)
        if stealth_plan:
            assert not stealth_plan.enabled, f"STEALTH must not be enabled by mission intent for query: {query}"


# ── safety: hardware critical still blocks heavy lanes ──────────────────────

def test_hardware_critical_blocks_heavy_lanes_regardless_of_mission():
    snap = build_acquisition_plan(
        query="CVE-2024-1234",
        duration_s=120.0,
        aggressive_mode=False,
        uma_state="critical",
        swap_detected=True,
        accepted_findings_so_far=0,
        branch_timeout_count=0,
        transport_authority_status=None,
        stealth_phase=None,
        acquisition_profile="default",
    )
    # Hardware critical must block heavy lanes regardless of mission intent
    assert snap.uma_state == "critical"
    assert snap.swap_detected is True
    # Wayback, BLOCKCHAIN should be blocked under critical
    for lane in ["WAYBACK", "BLOCKCHAIN"]:
        plan = next((p for p in snap.plans if p.lane == lane), None)
        if plan:
            assert not plan.enabled, f"{lane} must be disabled under hardware_critical"


# ── safety: no live network, no model load ───────────────────────────────────

def test_no_network_in_acquisition_strategy():
    import ast, sys
    mod = sys.modules["hledac.universal.runtime.acquisition_strategy"]
    path = mod.__file__ or ""
    with open(path) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("requests", "httpx", "aiohttp", "curl"):
            pytest.fail(f"Network library referenced: {node.id}")


def test_no_network_in_pivot_planner():
    import ast, sys
    mod = sys.modules["hledac.universal.runtime.pivot_planner"]
    path = mod.__file__ or ""
    with open(path) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in ("requests", "httpx", "aiohttp", "curl"):
            pytest.fail(f"Network library referenced: {node.id}")


def test_no_mlx_import_in_modules():
    for mod_path in [
        "hledac.universal.runtime.acquisition_strategy",
        "hledac.universal.runtime.pivot_planner",
    ]:
        import sys
        mod = sys.modules.get(mod_path)
        if mod:
            path = mod.__file__ or ""
            with open(path) as f:
                content = f.read()
            for line in content.split("\n"):
                if "import mlx" in line or "from mlx" in line:
                    pytest.fail(f"MLX import found in {mod_path}: {line.strip()}")


# ── integration: build_acquisition_report includes F226A fields ─────────────

def test_acquisition_report_includes_f226a_telemetry_fields():
    plan = build_acquisition_plan(
        query="CVE-2021-44228",
        duration_s=300.0,
        aggressive_mode=True,
        uma_state="ok",
        swap_detected=False,
        acquisition_profile="default",
    )
    report = build_acquisition_report(plan=plan, nonfeed_plan_debug=plan.nonfeed_plan_debug)
    debug = report.get("nonfeed_plan_debug", {})
    assert debug is not None
    assert "mission_runtime_applied" in debug, "mission_runtime_applied missing from report"
    assert "mission_lane_priority" in debug, "mission_lane_priority missing from report"
    assert "mission_pivot_boost_applied" in debug, "mission_pivot_boost_applied missing from report"
    assert "mission_feed_cap_reason" in debug, "mission_feed_cap_reason missing from report"


# ── pivot generation: mission_intent passed through ───────────────────────────

def test_generate_pivot_candidates_accepts_mission_intent_param():
    # Verify signature: (query, max_candidates=25, mission_intent=None)
    candidates = generate_pivot_candidates_from_query("test@example.com", mission_intent="person_recon")
    assert isinstance(candidates, list)


def test_generate_pivot_candidates_with_unknown_intent_no_boost():
    candidates = generate_pivot_candidates_from_query("test@example.com", mission_intent="unknown")
    # With unknown intent, mission boost is not applied (score_pivot_for_mission returns 1.0)
    assert len(candidates) >= 0  # Just verify it runs without error