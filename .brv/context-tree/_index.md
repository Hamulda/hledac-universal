---
children_hash: 11583c2d6248c51119bed66eab9a4b6a1eccb6710a103354d7ed2ba8a712776c
compression_ratio: 0.1798236179823618
condensation_order: 3
covers: [architecture/_index.md, data/_index.md, facts/_index.md, knowledge_base/_index.md, memory/_index.md, testing/_index.md]
covers_token_total: 4649
summary_level: d3
token_count: 836
type: summary
---
<think>
The user wants me to create a d1 structural summary for the testing/ domain based on the provided child entries. Let me analyze what I have and create a comprehensive summary.

From the context:
1. **url_deduplication_tests.md** - Test suite for URL dedup with RotatingBloomFilterAdapter
2. **exit_codes.md** - Exit code conventions (system_exit_codes.md, exit_code_convention.md)
3. **memory_leaks.md** - Memory leak tracking with objgraph, pytest-leaks, tracemalloc
4. **conftest/_index.md** - Pytest fixtures
5. **discovery_adapter_tests/_index.md** - Adapter tests
6. **discovery_adapters/_index.md** - Adapter implementations
7. **url_dedup_tests/_index.md** - URL dedup tests
8. **exit_codes/_index.md** - Exit code tests

Key facts to preserve:
- exit_code conventions (system_exit_codes.md, exit_code_convention.md)
- 6 tests in tests/test_exit_codes.py
- smoke_runner.py and smoke_runner_exit_code.py patterns
- memory leak detection tools
- url dedup test patterns
- fixture patterns

Let me write a clean d1 summary.
</think>

# Testing

## Exit Codes

| Code | Meaning | Example |
|------|---------|---------|
| 0 | Success | clean_exit |
| 1 | CLI error | ArgParse, invalid input |
| 2 | Import failure | Module not found |
| 3 | Config error | Missing HLEDAC_ROOT |
| 4 | Runtime error | Exception caught |
| 5 | Resource exhaustion | OOM, disk full |
| 6 | Test failure | pytest failed |
| 7 | Timeout | fetch timeout |
| 8 | Validation error | Schema mismatch |
| 9 | Internal error | Uncaught exception |

**Source:** `system_exit_codes.md`, `exit_code_convention.md`

**Tests:** `tests/test_exit_codes.py` — 6 tests validating smoke_runner patterns:
- `test_smoke_runner_success` — exit 0
- `test_smoke_runner_cli_error` — exit 1
- `test_smoke_runner_timeout` — exit 7
- `test_smoke_runner_import_error` — exit 2
- `test_smoke_runner_validation_error` — exit 8
- `test_clean_exit_with_no_fatal_signals`

**Runners:** `smoke_runner.py` (blocking), `smoke_runner_exit_code.py` (returns int)

## Memory Leak Detection

**Tools:** objgraph, pytest-leaks, tracemalloc, pytest-randomly

**Pattern:** Pytest-leaks plugin tracks reference cycles and object growth. Use `--preallocate-module=1 --preallocate-classes=500` for leak injection.

**Source:** `memory_leaks.md`

## URL Deduplication Tests

**File:** `tests/test_f_a5_url_dedup.py`

**API:** `dedupe_url_list(urls, filter=None, normalize=True)` → `(unique_urls, dropped_count)`

| Section | Tests | Purpose |
|---------|-------|---------|
| Basic correctness | 5 | First-seen-wins, correct counts, normalize flag |
| Filter mutation | 2 | Assert filter is mutated after dedupe |
| Edge cases | 3 | Unparseable URLs kept but excluded from filter |
| None filter fallback | 1 | List-only dedup without side effects |
| Real-world | 1 | 1800 raw → 600 unique across 3 batches |

**Isolation:** `fresh_filter` fixture provides per-test `RotatingBloomFilterAdapter`

**Source:** `url_deduplication_tests.md`, `url_dedup_tests/_index.md`

## Test Fixtures

**`conftest.py` patterns:**
- `fresh_filter` — per-test bloom filter isolation
- `mocker` — pytest-mock integration
- `tmp_path` — temp directory for outputs

**Source:** `conftest/_index.md`

## Discovery Adapters

**Tests:** `discovery_adapter_tests/_index.md`
**Implementations:** `discovery_adapters/_index.md`