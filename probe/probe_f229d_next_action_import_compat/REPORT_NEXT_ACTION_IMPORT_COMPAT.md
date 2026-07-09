# F229D: NEXT ACTION IMPORT COMPATIBILITY SEAL

**Result:** ALL PASS

**Date:** 2026-05-10

## Checks

| Check | Status | Detail |
|-------|--------|--------|
| lsm_file_exists | PASS | /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/benchmarks/live_spr |
| nam_file_exists | PASS | /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/benchmarks/live_mea |
| lsm_imports_derive_next_action | PASS | import names: ['NextActionInput', '_derive_next_action', '_was_family_attempted' |
| lsm_imports_next_action_input | PASS | import names: ['NextActionInput', '_derive_next_action', '_was_family_attempted' |
| lsm_imports_was_family_attempted | PASS | import names: ['NextActionInput', '_derive_next_action', '_was_family_attempted' |
| lsm_no_local_next_action_input | PASS |  |
| lsm_no_local_rule_helpers | PASS |  |
| lsm_no_local_was_family_attempted | PASS |  |
| lsm_no_local_derive_next_action | PASS |  |
| nam_exports_expected_symbols | PASS | found: ['NextActionInput', '_derive_next_action', '_was_family_attempted'] |
| nam_import_succeeds | PASS |  |
| nam_derive_next_action_callable | PASS |  |
| nam_next_action_input_fields | PASS | missing: set() |
| nam_rule_count | PASS | found 8: ['_rule_wallclock_enforcement', '_rule0b_memory_or_swap_gate', '_rule0g |
| nam_was_family_attempted_callable | PASS |  |
| nam_minimal_input_produces_tuple | PASS | got: ('unknown', None) |
| nam_was_family_attempted_findings>0 | PASS | got: True |
| nam_was_family_attempted_timed_out | PASS | got: True |
| nam_was_family_attempted_never | PASS | got: False |
