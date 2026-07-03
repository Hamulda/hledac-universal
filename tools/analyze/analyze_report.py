#!/usr/bin/env python3
"""Comprehensive sprint report analysis"""
import json, sys

REPORT = "/Users/vojtechhamada/.hledac/reports/8sa_1782562379071_994960_report.json"
with open(REPORT) as f:
    d = json.load(f)

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

section("SPRINT METADATA")
print(f"  synthesis_engine: {d.get('synthesis_engine_used')}")
print(f"  runtime_accepted_findings: {d.get('runtime_accepted_findings')}")
print(f"  gnn_predicted_links: {d.get('gnn_predicted_links')}")
print(f"  identity_candidates_found: {d.get('identity_candidates_found')}")
print(f"  identity_findings_produced: {d.get('identity_findings_produced')}")
print(f"  findings_per_minute: {d.get('findings_per_minute')}")
print(f"  actual_duration_s: {d.get('actual_duration_s')}")
print(f"  requested_duration_s: {d.get('requested_duration_s')}")
print(f"  elapsed_pct: {d.get('elapsed_pct')}")
print(f"  active_window_budget_s: {d.get('active_window_budget_s')}")
print(f"  active_window_elapsed_s: {d.get('active_window_elapsed_s')}")
print(f"  top_graph_nodes: {d.get('top_graph_nodes')}")

section("EARLY EXIT")
print(f"  early_exit_class: {d.get('early_exit_class')}")
print(f"  early_exit_reason: {d.get('early_exit_reason')}")
print(f"  scheduler_exit: {d.get('scheduler_exit')}")

section("PHASE DURATIONS")
for k,v in sorted(d.get("phase_duration_seconds",{}).items()):
    print(f"  {k}: {v}s")

section("ACQUISITION PRELUDE")
print(f"  ran: {d.get('acquisition_prelude_ran')}")
print(f"  checked: {d.get('acquisition_prelude_checked')}")
print(f"  duration: {d.get('acquisition_prelude_duration_s'):.4f}s")
print(f"  reason: {d.get('acquisition_prelude_reason')}")
print(f"  required_lanes: {d.get('acquisition_prelude_required_lanes')}")
print(f"  skipped_lanes: {d.get('acquisition_prelude_skipped_lanes')}")
print(f"  terminal_lanes: {d.get('acquisition_prelude_terminal_lanes')}")
prelude_errors = d.get('acquisition_prelude_errors', {})
print(f"  errors: {list(prelude_errors.keys()) if prelude_errors else 'none'}")

section("ACQUISITION TERMINALITY")
print(f"  checked: {d.get('acquisition_terminality_checked')}")
print(f"  satisfied: {d.get('acquisition_terminality_satisfied')}")
print(f"  missing_lanes: {d.get('acquisition_terminality_missing_lanes')}")
term_report = d.get('acquisition_terminality_report', {})
if term_report:
    for k,v in term_report.items():
        print(f"    {k}: {v}")

section("ACQUISITION REPORT")
acq_report = d.get('acquisition_report', {})
if acq_report:
    for k,v in acq_report.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for k2,v2 in v.items():
                print(f"    {k2}: {v2}")
        else:
            print(f"  {k}: {v}")
else:
    print("  (empty)")

section("SOURCE FAMILY OUTCOMES")
sfo = d.get('source_family_outcomes', {})
for family, outcome in sfo.items():
    print(f"  {family}: {outcome}")

section("LANES")
lanes = d.get("lanes", {})
if lanes:
    for lane_name in sorted(lanes.keys()):
        ld = lanes[lane_name]
        print(f"\n  [{lane_name}]")
        for k in sorted(ld.keys()):
            v = ld[k]
            if isinstance(v, dict):
                print(f"    {k}: {json.dumps(v)[:200]}")
            elif isinstance(v, list):
                print(f"    {k}: list[{len(v)}]")
            else:
                print(f"    {k}: {repr(v)[:200]}")
else:
    print("  (empty)")

section("DUCKDB STATS")
ddb = d.get("duckdb_stats", {})
for k,v in sorted(ddb.items()):
    print(f"  {k}: {v}")

section("MEMORY STATS")
for k,v in sorted(d.get("memory_stats", {}).items()):
    print(f"  {k}: {v}")

section("RUST EXTENSIONS")
for k,v in sorted(d.get("rust_extensions", {}).items()):
    print(f"  {k}: {v}")

section("PROVIDER YIELD DIAGNOSIS")
for k,v in d.get("provider_yield_diagnosis", {}).items():
    print(f"  {k}: {v}")

section("ENGINEERING ACTION MAP")
for k,v in d.get("engineering_action_map", {}).items():
    print(f"  {k}: {v}")

section("EXPECTED EVIDENCE")
for k,v in d.get("expected_evidence", {}).items():
    print(f"  {k}: {v}")

section("RETURN GUARD")
rg = d.get("return_guard", {})
if rg:
    for k,v in rg.items():
        print(f"  {k}: {v}")
else:
    print("  (empty)")

section("WINDUP GUARD")
wg = d.get("windup_guard_observation", {})
if wg:
    for k,v in wg.items():
        print(f"  {k}: {v}")
else:
    print("  (empty)")

section("PREWINDUP BARRIER")
pwb = d.get("prewindup_barrier", {})
if pwb:
    for k,v in pwb.items():
        print(f"  {k}: {v}")
else:
    print("  (empty)")

section("CANONICAL RUN SUMMARY")
crs = d.get("canonical_run_summary", {})
if crs:
    for k,v in crs.items():
        print(f"  {k}: {v}")
else:
    print("  (empty)")

section("ANALYST BRIEF")
ab = d.get("analyst_brief", {})
if ab:
    for k,v in ab.items():
        if isinstance(v, (dict, list)):
            print(f"  {k}: {type(v).__name__}[{len(v)}]")
        else:
            print(f"  {k}: {repr(v)[:200]}")

section("RUNTIME TRUTH")
rt = d.get("runtime_truth", {})
if rt:
    for k,v in rt.items():
        print(f"  {k}: {v}")
else:
    print("  (empty)")

section("TIMING TRUTH")
tt = d.get("timing_truth", {})
if tt:
    for k,v in tt.items():
        print(f"  {k}: {v}")
else:
    print("  (empty)")

section("CONTRACT STATUS")
print(f"  contract_status: {d.get('contract_status')}")
print(f"  minimum_success: {d.get('minimum_success')}")
print(f"  missing_critical: {d.get('missing_critical')}")
print(f"  unexpected_skipped: {d.get('unexpected_skipped')}")
print(f"  expected_families: {d.get('expected_families')}")

print("\n" + "="*60)
print("  PRODUCT VALUE SUMMARY")
print("="*60)
pvs = d.get("product_value_summary", {})
if pvs:
    for k,v in pvs.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for k2,v2 in v.items():
                print(f"    {k2}: {v2}")
        else:
            print(f"  {k}: {v}")
else:
    print("  (empty)")

print("\n" + "="*60)
print("  INVESTIGATION PACKET")
print("="*60)
ip = d.get("investigation_packet", {})
if ip:
    for k,v in ip.items():
        if isinstance(v, (dict, list)):
            print(f"  {k}: {type(v).__name__}[{len(v)}]")
        else:
            print(f"  {k}: {repr(v)[:200]}")
else:
    print("  (empty)")

print("\n" + "="*60)
print("  CAPABILITY SYNTHESIS")
print("="*60)
cs = d.get("capability_synthesis", {})
if cs:
    for k,v in cs.items():
        print(f"  {k}: {v}")
else:
    print("  (empty)")

# Check for any remaining important keys
section("ALL VALUES (full)")
for k in sorted(d.keys()):
    v = d[k]
    if isinstance(v, (dict, list)) and len(v) == 0:
        continue
    if k in ('findings', 'raw_findings', 'all_findings'):
        print(f"  {k}: list[{len(v)}]")
    elif isinstance(v, dict):
        print(f"  {k}: dict[{len(v)}] — {json.dumps(dict(list(v.items())[:3]))}...")
    elif isinstance(v, str) and len(v) > 200:
        print(f"  {k}: str[{len(v)}] = {v[:100]}...")
    else:
        print(f"  {k}: {repr(v)[:200]}")
