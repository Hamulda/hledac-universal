# F227D/F228G Live Measurement Extraction Guard

**Verdict:** `FAIL_TERMINALITY_SHADOWING`

## Checks

| Check | Pass | Detail |
| --- | --- | --- |
| extracted_module_exists_schema | PASS | /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/benchmarks/live_measurement_schema.py |
| extracted_module_exists_parser | PASS | /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/benchmarks/live_measurement_parser.py |
| extracted_module_exists_markdown | PASS | /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/benchmarks/live_measurement_markdown.py |
| runner_imports_schema | PASS | OK |
| schema_classes_not_in_runner | PASS | Found: OK |
| render_md_delegation | PASS | OK |
| parse_sprint_report_delegation | PASS | OK |
| schema_no_runtime_import | PASS | OK |
| parser_no_runtime_import | PASS | OK |
| markdown_no_runtime_import | PASS | OK |
| live_measurement_parser_has_required_exports | PASS | OK |
| live_measurement_markdown_has_required_exports | PASS | OK |
| quality_helpers_not_shadowed | FAIL | Violations: [{'name': '_uma_state_is_critical_or_emergency', 'line': 159, 'reason': 'local definition — body is not single delegation to imported helper'}, {'name': '_is_active_domain_query', 'line': 164, 'reason': 'local definition — body is not single delegation to imported helper'}, {'name': '_derive_run_quality_verdict', 'line': 179, 'reason': 'local definition — body is not single delegation to imported helper'}] |
| terminality_helpers_not_shadowed | FAIL | Violations: [{'name': '_has_terminal_source_outcomes', 'line': 169, 'reason': 'local definition — body is not single delegation to imported helper'}, {'name': '_has_scheduler_exit_path', 'line': 174, 'reason': 'local definition — body is not single delegation to imported helper'}] |
| live_kpi_input_wiring | PASS | OK |

## Extracted Modules

- **schema**: `benchmarks/live_measurement_schema.py`
- **parser**: `benchmarks/live_measurement_parser.py`
- **markdown**: `benchmarks/live_measurement_markdown.py`

## Schema Classes (must stay in schema module)

- `MeasurementStatus`
- `RunMode`
- `LiveMeasurementResult`
- `RunQualityVerdict`

## F228G Shadow Guard

### Quality Helpers (from live_measurement_quality.py)
- `_derive_run_quality_verdict`
- `_uma_state_is_critical_or_emergency`
- `_is_active_domain_query`

### Terminality Helpers (from live_measurement_parser.py)
- `_has_terminal_source_outcomes`
- `_has_scheduler_exit_path`

### LiveKpiInput Wiring Rules
- `LiveKpiInput` dataclass must exist
- `_derive_live_kpi_from_input` must have exactly one param: `inp`
- Function body must use `inp.attr` not bare `attr`