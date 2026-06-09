#!/usr/bin/env python3
"""Deep sprint report analysis."""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/Users/vojtechhamada/.hledac/reports/8sa_1780756273297_7d9878_report.json"
with open(path) as f:
    r = json.load(f)

# 1. Full acquisition report
acq = r.get("acquisition_report", {})
print("=== ACQUISITION REPORT (all keys) ===")
for k in sorted(acq.keys()):
    v = acq[k]
    if isinstance(v, dict):
        print(f"  {k}: dict({len(v)} keys)")
    elif isinstance(v, list):
        print(f"  {k}: list({len(v)} items) — {v[:3] if v else '[]'}")
    elif isinstance(v, str) and len(v) > 120:
        print(f"  {k}: {v[:120]}...")
    else:
        print(f"  {k}: {v!r}")

# 2. Public discovery details
print("\n=== PUBLIC DISCOVERY ===")
for k in ["public_terminal_stage", "public_discovered", "public_accepted_findings",
          "public_error", "public_discovery_empty_reason", "public_discovery_debug_reason",
          "public_provider_selection_debug", "public_stage_counters",
          "public_bootstrap_order", "public_bootstrap_prevented_discovery_timeout",
          "public_bootstrap_first_fetch_attempted"]:
    if k in r:
        v = r[k]
        if isinstance(v, dict) and len(str(v)) > 200:
            print(f"  {k}: dict({len(v)} keys)")
        else:
            print(f"  {k}: {v!r}")

# 3. CT details
print("\n=== CT DISCOVERY ===")
for k in ["ct_terminal_stage", "ct_planned", "ct_scheduled", "ct_provider_selected",
          "ct_request_attempted", "ct_raw_count", "ct_error", "ct_provider_status",
          "ct_log_discovered", "ct_log_accepted_findings"]:
    if k in r:
        print(f"  {k}: {r[k]!r}")

# 4. Feed details
print("\n=== FEED ===")
for k in ["feed_zero_yield_detected", "feed_inaccessible_detected", "feed_content_empty_detected",
          "feed_no_pattern_with_content", "feed_no_signal_sources", "dominant_feed_blocker"]:
    if k in r:
        print(f"  {k}: {r[k]!r}")

# 5. Nonfeed surface
print("\n=== NONFEED SURFACE ===")
for k in ["nonfeed_expected_lanes", "nonfeed_missing_expected_lanes",
          "nonfeed_surface_complete", "wayback_terminal_state", "passive_dns_terminal_state",
          "nonfeed_mission_active", "nonfeed_any_accepted"]:
    if k in r:
        print(f"  {k}: {r[k]!r}")

# 6. DOH lane
print("\n=== DOH LANE ===")
for k in ["doh_planned", "doh_scheduled", "doh_request_attempted", "doh_accepted_findings",
          "doh_terminal_stage"]:
    if k in r:
        print(f"  {k}: {r[k]!r}")

# 7. Seed context
print("\n=== SEED CONTEXT ===")
for k in ["seed_context_available", "seed_context_propagated", "seed_context_skip_reason",
          "lanes_unlocked_by_seed_context", "pivot_seed_domains", "pivot_seed_ips"]:
    if k in r:
        print(f"  {k}: {r[k]!r}")

# 8. Branch degradation
print("\n=== BRANCH DEGRADATION ===")
for k in ["branch_degradation_summary", "dominant_branch_blocker",
          "dominant_public_blocker", "dominant_feed_blocker"]:
    if k in r:
        print(f"  {k}: {r[k]!r}")

# 9. Capability synthesis
print("\n=== CAPABILITY SYNTHESIS ===")
cs = r.get("capability_synthesis", {})
for k, v in cs.items():
    print(f"  {k}: {v!r}")

# 10. Product value summary (selected)
pvs = r.get("product_value_summary", {})
print("\n=== PRODUCT VALUE (selected) ===")
for k in ["signal_stage", "winning_source", "feed_confidence_score",
          "zero_signal_reason", "evidence_freshness", "branch_value",
          "sprint_verdict", "query_effectiveness"]:
    if k in pvs:
        print(f"  {k}: {pvs[k]!r}")
