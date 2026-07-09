# F227D Live Measurement Extraction Guard

**Verdict:** `EXTRACTION_GUARD_PASS`

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

## Extracted Modules

- **schema**: `benchmarks/live_measurement_schema.py`
- **parser**: `benchmarks/live_measurement_parser.py`
- **markdown**: `benchmarks/live_measurement_markdown.py`

## Schema Classes (must stay in schema module)

- `RunMode`
- `MeasurementStatus`
- `RunQualityVerdict`
- `LiveMeasurementResult`