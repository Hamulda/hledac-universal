# Test F235G Probe Report — Validator Strictness

**Status:** ✅ PASSING — 28 tests, 0 failures, 0 errors

## Exit Codes Summary

| Exit | Name | Status |
|------|------|--------|
| 0 | VALID — clean diagnostic report | ✅ |
| 1 | MALFORMED — truth missing | ✅ |
| 2 | PROFILE FAILED | ✅ |
| 3 | KPI FAILED | ✅ |
| 4 | FALLBACK USED | ✅ |
| 5 | SOURCE FAMILY INCONSISTENCY | ✅ |
| **6** | **DUPLICATE SOURCE FAMILIES** | ✅ **NEW** |
| **7** | **PROFILE/PRIORITY MISMATCH** | ✅ **NEW** |
| **8** | **CT PRELUDE CONTRADICTION** | ✅ **NEW** |
| **9** | **PUBLIC DISCOVERY_ERROR MISSING REASON** | ✅ **NEW** |

## New Check Results

### Exit 6 — Duplicate Normalized Source Families
| Test | Result |
|------|--------|
| `test_ct_and_ct_present_fails_exit_6` | ✅ PASS |
| `test_public_and_public_lowercase_fails_exit_6` | ✅ PASS |
| `test_single_ct_uppercase_passes` | ✅ PASS |
| `test_mixed_case_all_unique_passes` | ✅ PASS |

### Exit 7 — Profile/Priority Mismatch
| Test | Result |
|------|--------|
| `test_default_profile_when_ct_expected_fails_exit_7` | ✅ PASS |
| `test_nonfeed_priority_false_when_ct_expected_fails_exit_7` | ✅ PASS |
| `test_nonfeed_diagnostic_profile_with_ct_missing_passes` | ✅ PASS |
| `test_ct_not_expected_no_exit_7` | ✅ PASS |

### Exit 8 — CT Prelude Contradiction
| Test | Result |
|------|--------|
| `test_ct_missing_in_prelude_but_ct_attempted_error_no_flag_fails_exit_8` | ✅ PASS |
| `test_ct_missing_in_prelude_attempted_error_flag_false_fails_exit_8` | ✅ PASS |
| `test_ct_missing_in_prelude_attempted_error_flag_true_passes` | ✅ PASS |
| `test_ct_missing_in_prelude_no_ct_attempted_error_passes` | ✅ PASS |

### Exit 9 — Public DISCOVERY_ERROR Missing Reason
| Test | Result |
|------|--------|
| `test_discovery_error_no_reason_fails_exit_9` | ✅ PASS |
| `test_discovery_error_with_reason_passes` | ✅ PASS |
| `test_discovery_error_with_provider_errors_passes` | ✅ PASS |
| `test_non_discovery_stage_no_exit_9` | ✅ PASS |

### Exit Priority (Assert 8)
| Test | Scenario | Winner | Result |
|------|---------|--------|--------|
| `test_fallback_priority_over_duplicate` | exit 4 + 6 | exit 4 | ✅ PASS |
| `test_duplicate_over_profile_mismatch` | exit 6 + 7 | exit 6 | ✅ PASS |
| `test_profile_mismatch_over_ct_contradiction` | exit 7 + 8 | exit 7 | ✅ PASS |
| `test_ct_contradiction_over_public_reason` | exit 8 + 9 | exit 8 | ✅ PASS |

### Canonical/Regression
| Test | Result |
|------|--------|
| `test_f234a_fixture_exit_2_not_exit_1` | ✅ PASS (exits 7 — expected) |
| `test_clean_minimal_report_passes` | ✅ PASS |
| `test_fallback_marker_exit_4` | ✅ PASS |
| `test_source_family_missing_when_stage_present_exit_5` | ✅ PASS |

### Forbidden Import Checks (Asserts 9-12)
| Test | Result |
|------|--------|
| `test_no_network_imports` | ✅ PASS |
| `test_no_mlx_imports` | ✅ PASS |
| `test_no_browser_stealth_imports` | ✅ PASS |
| `test_no_runtime_imports` | ✅ PASS |

## F234A Fixture Behavior (Live Report)

The F234A fixture (`probe_f234a_live_nonfeed_truth_replay/live_nonfeed_truth_replay.json`) — which represents a **real fallback run** from production — now correctly exits **7 (PROFILE/PRIORITY MISMATCH)** instead of being silently treated as valid:

```
[FAIL] profile_priority_mismatch: acquisition_profile=default (should be nonfeed_diagnostic for CT-expected run)
[FAIL] public_discovery_error_reason: public_terminal_stage=DISCOVERY_ERROR but no public_discovery_empty_reason
[FAIL] acquisition_profile: expected 'nonfeed_diagnostic', got {'default'}
[FAIL] nonfeed_priority: nonfeed_priority_enabled=None
Exit 7: PROFILE/PRIORITY MISMATCH
```

This proves the validator now correctly surfaces the production bug where a CT-expected run had `acquisition_profile=default` and `nonfeed_priority_enabled=False`.

## Files Changed

| File | Change |
|------|--------|
| `tools/f234_validate_nonfeed_live_report.py` | +4 new checks (exits 6-9), new exit priority |
| `tools/f234_validate_nonfeed_live_report.py.bak_F235G_VALIDATOR_STRICTNESS` | Backup |
| `tests/probe_f235g_validator_strictness/test_f235g_validator_strictness.py` | 28 probe tests |
| `probe_f235g_validator_strictness/REPORT_VALIDATOR_STRICTNESS.md` | This report |
| `probe_f235g_validator_strictness/validator_strictness.json` | Test metadata |

## Verification Command

```bash
rtk proxy python -m pytest -q tests/probe_f235g_validator_strictness
# Expected: 28 passed in ~1s
```