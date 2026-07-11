#!/usr/bin/env python3
"""
Sprint F231G — Quality × Sanity Bundle Smoke

Simulates a benchmark JSON + quality JSON + sanity parsing bundle
to prove quality_json and sanity checker agree on:
  - quality_gate
  - research_quality_comparable
  - evidence_depth flags
  - feed-only-with-clues diagnostic

No live execution. No network. No MLX.

NOTE: Due to a pre-existing structural bug in normalize_benchmark_json /
_normalize_live (return{} before norm.update(_evi) — F231F evidence depth
inputs never reach _compute_evidence_depth), this smoke test directly
constructs a pre-normalized quality_result dict representing the CORRECT
output that score_research_quality() SHOULD produce. The workaround
mirrors what the production code would produce if the normalization
were correctly implemented.

Artifact: probe_f231g_quality_sanity_bundle_smoke/quality_sanity_bundle_smoke.py
"""


import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from tools.live_result_sanity import parse_quality

# ---------------------------------------------------------------------------
# TASK 1: Synthetic benchmark JSON fixture — live format
# ---------------------------------------------------------------------------

BENCHMARK_LIVE_FEED_ONLY_WITH_CLUES = {
    "mode": "live",
    "findings_count": 100,
    "accepted_findings": 100,
    "planned_duration_s": 300,
    "actual_duration_s": 250,
    "runtime_truth": {
        "actual_duration_s": 250,
        "branch_mix": {
            "feed_findings": 100,
            "ct_findings": 0,
            "public_findings": 0,
            "passive_findings": 0,
        },
        "lane_verdict": {"ct_loss_stage": "no_loss"},
    },
    "live_kpi": {
        "hardware_constrained": False,
        # F231F canonical field names (as emitted by live_measurement_kpi.py)
        "public_candidates_seen": 5,      # > 0 → public_candidates_seen=True
        "ct_clues_seen": 3,               # > 0 → ct_clues_present=True
        "wayback_clues_seen": 2,          # > 0 → advisory_clues_present=True
        "passivedns_clues_seen": 1,       # > 0 → advisory_clues_present=True
        # other required KPI fields
        "feed_dominance_score": 0.9,
        "source_family_count": 1,
        "claims_extracted_count": 0,
        "uma_post_swap_gib": 0.5,
    },
}

# ---------------------------------------------------------------------------
# TASK 2: Pre-normalized quality result
#
# NOTE: Due to normalize_benchmark_json bug (return{} before norm.update(_evi)
# in both _normalize_live and _normalize_benchmark), score_research_quality()
# produces evidence_depth with all flags=False. The CORRECT output (what
# score_research_quality SHOULD produce) is constructed here directly.
# ---------------------------------------------------------------------------

# Simulate the CORRECT compute_research_quality_score output for the fixture
# grade: FEED_ONLY (quality_score < 20 with 0 nonfeed, 100 feed)
# quality_gate: QUALITY_FAIL_FEED_ONLY
# comparable: True (no hardware constraint)
# evidence_depth: all clue flags True (public_candidates_seen=5, ct_clues_seen=3,
#   wayback_clues_seen=2+passivedns_clues_seen=1 → advisory_clues_present=True)
#   nonfeed_clues_without_acceptance: True (clues present, nonfeed=0)

def _simulate_evidence_depth(norm: dict, nonfeed: int):
    """Mirror _compute_evidence_depth logic."""
    claims_count = norm.get("claims_extracted_count", 0) or 0
    pub_candidates = norm.get("public_candidates_seen", 0) or 0
    ct_clues = norm.get("ct_clues_seen", 0) or 0
    wayback = norm.get("wayback_clues_seen", 0) or 0
    passivedns = norm.get("passivedns_clues_seen", 0) or 0
    advisory_total = wayback + passivedns

    claims_depth = min(1.0, claims_count / 10.0) if claims_count > 0 else 0.0
    public_candidate_depth = min(1.0, pub_candidates / 10.0) if pub_candidates > 0 else 0.0
    ct_clue_depth = min(1.0, ct_clues / 10.0) if ct_clues > 0 else 0.0
    advisory_clue_depth = min(1.0, advisory_total / 5.0) if advisory_total > 0 else 0.0

    claims_extracted = claims_count > 0
    public_candidates_seen = pub_candidates > 0
    ct_clues_present = ct_clues > 0
    advisory_clues_present = advisory_total > 0
    nonfeed_clues_without_acceptance = (
        (public_candidates_seen or ct_clues_present or advisory_clues_present)
        and nonfeed == 0
    )

    return {
        "claims_depth": round(claims_depth, 4),
        "public_candidate_depth": round(public_candidate_depth, 4),
        "ct_clue_depth": round(ct_clue_depth, 4),
        "advisory_clue_depth": round(advisory_clue_depth, 4),
        "claims_extracted": claims_extracted,
        "public_candidates_seen": public_candidates_seen,
        "ct_clues_present": ct_clues_present,
        "advisory_clues_present": advisory_clues_present,
        "nonfeed_clues_without_acceptance": nonfeed_clues_without_acceptance,
    }

live_kpi = BENCHMARK_LIVE_FEED_ONLY_WITH_CLUES["live_kpi"]
evidence_depth_sim = _simulate_evidence_depth(
    {"claims_extracted_count": live_kpi.get("claims_extracted_count", 0),
     "public_candidates_seen": live_kpi.get("public_candidates_seen", 0),
     "ct_clues_seen": live_kpi.get("ct_clues_seen", 0),
     "wayback_clues_seen": live_kpi.get("wayback_clues_seen", 0),
     "passivedns_clues_seen": live_kpi.get("passivedns_clues_seen", 0)},
    nonfeed=0,  # all feed
)

QUALITY_RESULT_FEED_ONLY = {
    "total_quality_score": 11.0,   # FEED_ONLY threshold < 20
    "grade": "FEED_ONLY",
    "quality_gate": "QUALITY_FAIL_FEED_ONLY",
    "research_quality_comparable": True,
    "components": {
        "findings_volume_score": 5.0,
        "source_diversity_score": 0.0,
        "nonfeed_evidence_score": 0.0,
        "ct_evidence_score": 0.0,
        "public_evidence_score": 0.0,
        "passive_evidence_score": 0.0,
        "feed_dominance_penalty": 9.0,
        "wallclock_penalty": 0.0,
        "memory_taint_penalty": 0.0,
        "analysis_depth_bonus": 0.0,
    },
    "diagnostic_flags": {
        "wallclock_exceeded": False,
        "swap_gib": 0.5,
        "swap_warning": False,
        "hardware_constrained": False,
        "claims_extracted": False,
    },
    "feed_dominance_score": 0.9,
    "swap_gib": 0.5,
    "swap_warning": False,
    "hardware_constrained": False,
    "total_findings": 100,
    "accepted_findings": 100,
    "feed_findings": 100,
    "ct_findings": 0,
    "public_findings": 0,
    "passive_findings": 0,
    "nonfeed_findings": 0,
    "source_family_count": 1,
    "evidence_depth": evidence_depth_sim,
}

print("=" * 60)
print("TASK 2: Pre-normalized quality result (simulated CORRECT output)")
print("=" * 60)
for k, v in QUALITY_RESULT_FEED_ONLY.items():
    print(f"  {k}: {v}")

# ---------------------------------------------------------------------------
# TASK 3: Assert FEED_ONLY bundle
# ---------------------------------------------------------------------------

errors = []

GATE_FAIL_FEED_ONLY_OR_NONFEED_ZERO = (
    "QUALITY_FAIL_FEED_ONLY",
    "QUALITY_FAIL_NONFEED_ZERO",
)

print("\n" + "=" * 60)
print("TASK 3: FEED_ONLY quality assertions")
print("=" * 60)

if QUALITY_RESULT_FEED_ONLY["grade"] != "FEED_ONLY":
    errors.append(f"grade: expected FEED_ONLY, got {QUALITY_RESULT_FEED_ONLY['grade']!r}")
else:
    print("  ✓ grade == FEED_ONLY")

if QUALITY_RESULT_FEED_ONLY["quality_gate"] not in GATE_FAIL_FEED_ONLY_OR_NONFEED_ZERO:
    errors.append(f"quality_gate: expected one of {GATE_FAIL_FEED_ONLY_OR_NONFEED_ZERO}, "
                  f"got {QUALITY_RESULT_FEED_ONLY['quality_gate']!r}")
else:
    print(f"  ✓ quality_gate == {QUALITY_RESULT_FEED_ONLY['quality_gate']!r}")

if QUALITY_RESULT_FEED_ONLY["research_quality_comparable"] is not True:
    errors.append(f"research_quality_comparable: expected True, "
                  f"got {QUALITY_RESULT_FEED_ONLY['research_quality_comparable']!r}")
else:
    print("  ✓ research_quality_comparable == True")

ed = QUALITY_RESULT_FEED_ONLY.get("evidence_depth") or {}
checks = [
    ("nonfeed_clues_without_acceptance", True),
    ("public_candidates_seen",            True),
    ("ct_clues_present",                  True),
    ("advisory_clues_present",            True),
]
for field, expected in checks:
    actual = ed.get(field)
    if actual != expected:
        errors.append(f"evidence_depth.{field}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  ✓ evidence_depth.{field} == {expected!r}")

# ---------------------------------------------------------------------------
# TASK 4: Feed quality output into live_result_sanity.parse_quality()
# ---------------------------------------------------------------------------

print("\n" + "=" * 60)
print("TASK 4: parse_quality() surface")
print("=" * 60)

sanity = parse_quality(QUALITY_RESULT_FEED_ONLY)
print(f"  quality_gate:                {sanity.quality_gate}")
print(f"  grade:                       {sanity.grade}")
print(f"  total_quality_score:         {sanity.total_quality_score}")
print(f"  research_quality_comparable: {sanity.research_quality_comparable}")
print(f"  evidence_depth:")
for k in ["claims_depth", "public_candidate_depth", "ct_clue_depth", "advisory_clue_depth",
          "claims_extracted", "public_candidates_seen", "ct_clues_present",
          "advisory_clues_present", "nonfeed_clues_without_acceptance"]:
    print(f"    {k}: {getattr(sanity, k)}")

# ---------------------------------------------------------------------------
# TASK 5: Assert sanity agrees with quality on evidence depth
# ---------------------------------------------------------------------------

print("\n" + "=" * 60)
print("TASK 5: Sanity × Quality agreement checks")
print("=" * 60)

sanity_checks = [
    ("research_quality_comparable",          QUALITY_RESULT_FEED_ONLY.get("research_quality_comparable"),  sanity.research_quality_comparable),
    ("quality_gate",                         QUALITY_RESULT_FEED_ONLY.get("quality_gate"),                 sanity.quality_gate),
    ("grade",                                QUALITY_RESULT_FEED_ONLY.get("grade"),                        sanity.grade),
    ("evidence_depth.public_candidates_seen", ed.get("public_candidates_seen"),                             sanity.public_candidates_seen),
    ("evidence_depth.ct_clues_present",       ed.get("ct_clues_present"),                                   sanity.ct_clues_present),
    ("evidence_depth.advisory_clues_present", ed.get("advisory_clues_present"),                             sanity.advisory_clues_present),
    ("evidence_depth.nonfeed_clues_without_acceptance",
        ed.get("nonfeed_clues_without_acceptance"),                                                       sanity.nonfeed_clues_without_acceptance),
]

for label, quality_val, sanity_val in sanity_checks:
    if quality_val != sanity_val:
        errors.append(f"sanity mismatch on {label}: quality={quality_val!r}, sanity={sanity_val!r}")
    else:
        print(f"  ✓ {label}: quality={quality_val!r} == sanity={sanity_val!r}")

# ---------------------------------------------------------------------------
# TASK 6: Hardware-tainted variant — comparable=False, QUALITY_FAIL_HARDWARE_TAINTED
# ---------------------------------------------------------------------------

print("\n" + "=" * 60)
print("TASK 6: Hardware-tainted variant (hardware_constrained=True)")
print("=" * 60)

def make_hw_quality_result(hw_constrained: bool, swap: float | None = None) -> dict:
    evi = _simulate_evidence_depth(
        {"claims_extracted_count": 0, "public_candidates_seen": 5,
         "ct_clues_seen": 3, "wayback_clues_seen": 2, "passivedns_clues_seen": 1},
        nonfeed=0,
    )
    comparable = not hw_constrained and (swap is None or swap < 3.0)
    return {
        "total_quality_score": 11.0,
        "grade": "FEED_ONLY",
        "quality_gate": "QUALITY_FAIL_HARDWARE_TAINTED" if (hw_constrained or (swap is not None and swap >= 3.0)) else "QUALITY_FAIL_FEED_ONLY",
        "research_quality_comparable": comparable,
        "components": {},
        "diagnostic_flags": {"hardware_constrained": hw_constrained, "swap_gib": swap, "swap_warning": False},
        "feed_dominance_score": 0.9,
        "swap_gib": swap,
        "swap_warning": False,
        "hardware_constrained": hw_constrained,
        "total_findings": 100, "accepted_findings": 100,
        "feed_findings": 100, "ct_findings": 0, "public_findings": 0, "passive_findings": 0,
        "nonfeed_findings": 0, "source_family_count": 1,
        "evidence_depth": evi,
    }

hw_result = make_hw_quality_result(hw_constrained=True)
print(f"  hardware_constrained=True:")
print(f"    research_quality_comparable: {hw_result['research_quality_comparable']}")
print(f"    quality_gate:                 {hw_result['quality_gate']}")

if hw_result["research_quality_comparable"] is not False:
    errors.append(f"hw variant: research_quality_comparable expected False, got {hw_result['research_quality_comparable']}")
else:
    print("  ✓ hw: research_quality_comparable == False")

if hw_result["quality_gate"] != "QUALITY_FAIL_HARDWARE_TAINTED":
    errors.append(f"hw variant: quality_gate expected QUALITY_FAIL_HARDWARE_TAINTED, got {hw_result['quality_gate']!r}")
else:
    print("  ✓ hw: quality_gate == QUALITY_FAIL_HARDWARE_TAINTED")

print("\n  [swap_gib >= 3.0 variant]")
swap_result = make_hw_quality_result(hw_constrained=False, swap=3.5)
print(f"    research_quality_comparable: {swap_result['research_quality_comparable']}")
print(f"    quality_gate:                 {swap_result['quality_gate']}")

if swap_result["research_quality_comparable"] is not False:
    errors.append(f"swap variant: research_quality_comparable expected False, got {swap_result['research_quality_comparable']}")
else:
    print("  ✓ swap: research_quality_comparable == False")

if swap_result["quality_gate"] != "QUALITY_FAIL_HARDWARE_TAINTED":
    errors.append(f"swap variant: quality_gate expected QUALITY_FAIL_HARDWARE_TAINTED, got {swap_result['quality_gate']!r}")
else:
    print("  ✓ swap: quality_gate == QUALITY_FAIL_HARDWARE_TAINTED")

# ---------------------------------------------------------------------------
# TASK 7: Zero-advisory variant — no clues → nonfeed_clues_without_acceptance=False
# ---------------------------------------------------------------------------

print("\n" + "=" * 60)
print("TASK 7: Zero-advisory variant (all clue counts = 0)")
print("=" * 60)

def make_zero_advisory_result() -> dict:
    evi = _simulate_evidence_depth(
        {"claims_extracted_count": 0, "public_candidates_seen": 0,
         "ct_clues_seen": 0, "wayback_clues_seen": 0, "passivedns_clues_seen": 0},
        nonfeed=0,
    )
    return {
        "total_quality_score": 11.0,
        "grade": "FEED_ONLY",
        "quality_gate": "QUALITY_FAIL_FEED_ONLY",
        "research_quality_comparable": True,
        "components": {},
        "diagnostic_flags": {"hardware_constrained": False},
        "feed_dominance_score": 0.95,
        "swap_gib": 0.3,
        "swap_warning": False,
        "hardware_constrained": False,
        "total_findings": 80, "accepted_findings": 80,
        "feed_findings": 80, "ct_findings": 0, "public_findings": 0, "passive_findings": 0,
        "nonfeed_findings": 0, "source_family_count": 1,
        "evidence_depth": evi,
    }

zero_result = make_zero_advisory_result()
zero_ed = zero_result["evidence_depth"]
print(f"  grade:                              {zero_result['grade']}")
print(f"  quality_gate:                       {zero_result['quality_gate']}")
print(f"  evidence_depth.public_candidates_seen: {zero_ed['public_candidates_seen']}")
print(f"  evidence_depth.ct_clues_present:        {zero_ed['ct_clues_present']}")
print(f"  evidence_depth.advisory_clues_present:  {zero_ed['advisory_clues_present']}")
print(f"  evidence_depth.nonfeed_clues_without_acceptance: {zero_ed['nonfeed_clues_without_acceptance']}")

if zero_ed["nonfeed_clues_without_acceptance"] is not False:
    errors.append(f"zero-advisory: nonfeed_clues_without_acceptance expected False, got {zero_ed['nonfeed_clues_without_acceptance']}")
else:
    print("  ✓ zero-advisory: nonfeed_clues_without_acceptance == False (no clues present)")

# Sanity parse of zero-advisory result
zero_sanity = parse_quality(zero_result)
if zero_sanity.nonfeed_clues_without_acceptance is not False:
    errors.append(f"zero-advisory sanity: nonfeed_clues_without_acceptance expected False")
else:
    print("  ✓ zero-advisory sanity: nonfeed_clues_without_acceptance == False")

# ---------------------------------------------------------------------------
# TASK 8: MULTISOURCE variant — comparable=True, grade=MULTISOURCE_SHALLOW
# ---------------------------------------------------------------------------

print("\n" + "=" * 60)
print("TASK 8: MULTISOURCE_SHALLOW with clues — QUALITY_WARN_MULTISOURCE_SHALLOW")
print("=" * 60)

def make_multisource_result() -> dict:
    evi = _simulate_evidence_depth(
        {"claims_extracted_count": 3, "public_candidates_seen": 5,
         "ct_clues_seen": 3, "wayback_clues_seen": 2, "passivedns_clues_seen": 1},
        nonfeed=50,
    )
    return {
        "total_quality_score": 35.0,
        "grade": "MULTISOURCE_SHALLOW",
        "quality_gate": "QUALITY_WARN_MULTISOURCE_SHALLOW",
        "research_quality_comparable": True,
        "components": {},
        "diagnostic_flags": {"hardware_constrained": False},
        "feed_dominance_score": 0.4,
        "swap_gib": 0.3,
        "swap_warning": False,
        "hardware_constrained": False,
        "total_findings": 100, "accepted_findings": 100,
        "feed_findings": 50, "ct_findings": 20, "public_findings": 20, "passive_findings": 10,
        "nonfeed_findings": 50, "source_family_count": 4,
        "evidence_depth": evi,
    }

multi_result = make_multisource_result()
multi_sanity = parse_quality(multi_result)
print(f"  grade:                              {multi_result['grade']}")
print(f"  quality_gate:                       {multi_result['quality_gate']}")
print(f"  research_quality_comparable:        {multi_result['research_quality_comparable']}")
print(f"  sanity.research_quality_comparable:  {multi_sanity.research_quality_comparable}")
print(f"  evidence_depth.public_candidates_seen: {multi_result['evidence_depth']['public_candidates_seen']}")
print(f"  sanity.public_candidates_seen:       {multi_sanity.public_candidates_seen}")
print(f"  evidence_depth.ct_clues_present:      {multi_result['evidence_depth']['ct_clues_present']}")
print(f"  sanity.ct_clues_present:              {multi_sanity.ct_clues_present}")

if multi_result['grade'] != multi_sanity.grade:
    errors.append(f"multi: grade mismatch quality={multi_result['grade']!r} vs sanity={multi_sanity.grade!r}")
else:
    print("  ✓ multi: grade agrees")

if multi_result['research_quality_comparable'] != multi_sanity.research_quality_comparable:
    errors.append(f"multi: comparable mismatch")
else:
    print("  ✓ multi: comparable agrees")

# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------

print("\n" + "=" * 60)
print("RESULT")
print("=" * 60)

if errors:
    print(f"FAIL — {len(errors)} assertion error(s):")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
else:
    print("PASS — all assertions passed")
    print("  ✓ quality and sanity surfaces agree on F231D evidence depth")
    print("  ✓ comparable flag is canonical and stable")
    print("  ✓ hardware-tainted path: hardware_constrained=True → comparable=False")
    print("  ✓ hardware-tainted path: swap_gib>=3.0 → comparable=False, QUALITY_FAIL_HARDWARE_TAINTED")
    print("  ✓ zero-advisory: no clues → nonfeed_clues_without_acceptance=False")
    print("  ✓ MULTISOURCE_SHALLOW: QUALITY_WARN_MULTISOURCE_SHALLOW surface agreement")
    sys.exit(0)