# One-Button Prelive Gate Report (F221H)

**Verdict:** 🟡 `READY_FOR_FEED_BASELINE_ONLY`
**Live Allowed:** `True`
**Profile:** `nonfeed_diagnostic180`
**Query:** `mozilla.org certificate transparency subdomains april 2026`

---

## Decision Summary

- Feed baseline ready. Nonfeed capability blocked: f232g_research_quality_missing

---

## UMA / Swap State

| system_used_gib | `0.0` |
| swap_used_gib | `0.0` |
| swap_detected | `False` |
| uma_state | `unknown` |
| io_only | `False` |
| error | `No module named 'core'` |

| Swap Policy Tier | `clean` |
| Swap Gate Reason | `swap=0.000GiB <= 2.0GiB` |

---

## F221 Artifact Status

| Total | 7 |
| Valid | 7 |
| Missing | 0 |

### F221 Artifact Details

| Probe | Artifact | Found | Valid |
|------|----------|-------|-------|
| probe_f221a_source_family_truth | source_family_truth.json | ✅ | ✅ |
| probe_f221b_ct_domain_lane | ct_domain_lane.json | ✅ | ✅ |
| probe_f221c_public_timeout_diagnosis | public_timeout_diagnosis.json | ✅ | ✅ |
| probe_f221d_quality_surface_consistency | quality_surface_consistency.json | ✅ | ✅ |
| probe_f221e_delta_sanity_alignment | delta_sanity_alignment.json | ✅ | ✅ |
| probe_f221f_ae_integration_guard | ae_integration_guard.json | ✅ | ✅ |
| probe_f221g_nonfeed_diag_ready | nonfeed_diag_ready.json | ✅ | ✅ |

---

## F223 Post-F223 Artifact Status (Sprint F224E)

| Required Total | 5 |
| Required Valid | 5 |
| Required Missing | 0 |
| Optional Total | 3 |
| Optional Valid | 3 |

### F223 Required Artifact Details

| Probe | Artifact | Found | Valid |
|------|----------|-------|-------|
| probe_f223a_nonfeed_profile_propagation | nonfeed_profile_propagation.json | ✅ | ✅ |
| probe_f223b_terminality_verdict_ssot | terminality_verdict_ssot.json | ✅ | ✅ |
| probe_f223c_public_counter_truth | public_counter_truth.json | ✅ | ✅ |
| probe_f223d_product_value_reality | product_value_reality.json | ✅ | ✅ |
| probe_f223h_cwd_invocation_guard | cwd_invocation_guard.json | ✅ | ✅ |

### F223 Optional Artifact Details

(_Optional — advisory only, does not block_)
| Probe | Artifact | Found | Valid |
|------|----------|-------|-------|
| probe_f223e_async_resource_hygiene | async_resource_hygiene.json | ✅ | ✅ |
| probe_f223f_analyst_brief_reality | analyst_brief_reality.json | ✅ | ✅ |
| probe_f223g_persistent_dedup_audit | persistent_dedup_audit.json | ✅ | ✅ |

---

## Provider Surface

- **OK:** ✅ `True`
- **Fallback Schema Blocked:** `False`

---

## F233F Gate: Capability vs Feed Split

| Field | Value |
|-------|-------|
| Live Allowed | `True` |
| Capability Live Allowed | `False` |
| Feed Baseline Allowed | `True` |
| Why Capability Blocked | `f232g_research_quality_missing` |
| Degraded But Allowed | `False` |
| Canonical Fallback Detected | `False` |
| F232G Research Quality Present | `False` |
| F233D Nonfeed Prelude Coverage | `False` |

### Exact Command (Feed Baseline)

_Nonfeed capability blocked. Feed baseline run:_
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac && rtk proxy python -m hledac.universal.benchmarks.live_sprint_measurement --profile nonfeed_diagnostic180 --query "mozilla.org certificate transparency subdomains april 2026" --live --require-memory-ok --output-json <path> --output-md <path>
```

---

## Live Command (Sprint F224E)

### Exact Command
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac && rtk proxy python -m hledac.universal.benchmarks.live_sprint_measurement --profile nonfeed_diagnostic180 --query "mozilla.org certificate transparency subdomains april 2026" --live --require-memory-ok --output-json <path> --output-md <path>
```

### Expected Post-F223 Assertions
- `benchmark_profile` → `nonfeed_diagnostic180`
- `acquisition_profile` → `nonfeed_diagnostic`
- `run_quality_verdict` → `PASS_VALID_CAPABILITY_RUN or FAIL_NONFEED_EVIDENCE_MISSING`
- `hardware_constrained` → `False`
- `capability_synthesis` → `not None`
- `next_sprint_seeds_generated` → `true or explicit skip_reason`
- `public_terminal_stage_not_discovery_timeout` → `when bootstrap candidates exist`
- `CT_raw_gt_0_accepted_eq_0_no_loss` → `False`
- `nonfeed_priority_enabled` → `True`
- `terminality_satisfied_cannot_produce_FAIL_TERMINALITY_UNSATISFIED` → `True`
- `FAIL_NONFEED_EVIDENCE_MISSING_when_nonfeed_evidence_missing` → `True`
- `runtime_accepted_findings_divergence_explicit` → `True`
- `public_stage_counters_raw_count_source_present` → `True`

### Abort Conditions
- **swap_above_2G:** swap > 2.0GiB
- **missing_f229_artifacts:** any F229 structural check fails
- **missing_f223_required_artifacts:** any F223 required artifact missing
- **fallback_acquisition_schema:** fallback_schema detected in prelive reports
- **capability_synthesis_missing_in_exporter_self_test:** capability_synthesis not in _generate_next_sprint_seeds
- **public_ct_provider_surface_missing:** provider surface not OK
- **uma_state_critical_or_emergency:** uma_state in (critical, emergency)

---

## How to Run This Gate

```bash
python tools/prelive_one_button_gate.py \
  --repo-root . \
  --profile nonfeed_diagnostic180 \
  --query "mozilla.org certificate transparency subdomains april 2026" \
  --output-json probe_f221h_one_button_prelive_gate/one_button_prelive_gate.json \
  --output-md probe_f221h_one_button_prelive_gate/REPORT_ONE_BUTTON_PRELIVE_GATE.md
```

With optional last-live triage:
```bash
python tools/prelive_one_button_gate.py \
  --repo-root . --profile nonfeed_diagnostic180 \
  --query "..." \
  --last-live-triage probe_f219g_live_artifact_triage/triage.json \
  --decision-gate-json probe_f219f_prelive_decision_gate/prelive_decision.json \
  --output-json ... --output-md ...
```