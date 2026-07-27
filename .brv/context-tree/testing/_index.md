---
children_hash: 9eb83f8d60ebd279a00bcd5805dc2f1dd87892cdbec93fc1eeddd8e855d4665b
compression_ratio: 0.14959531119173877
condensation_order: 2
covers: [conftest/_index.md, discovery_adapter_tests/_index.md, discovery_adapters/_index.md, exit_codes/_index.md, memory_leaks/_index.md, url_dedup/_index.md, url_dedup_tests/_index.md]
covers_token_total: 3583
summary_level: d2
token_count: 536
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the child entries. I need to preserve key facts, architectural decisions, and relationships while compressing to ~20-30% of the original token count. I should reference entry names for drill-down.

Let me analyze the single child entry:

**url_deduplication_tests.md** - A test suite for URL deduplication with the following key points:
- Test file: tests/test_f_a5_url_dedup.py
- 13 tests in 5 sections
- Tests RotatingBloomFilterAdapter isolation
- dedupe_url_list returns (unique_list, dropped_count)
- Edge cases like unparseable URLs, None filter fallback
- Fresh filter fixture pattern for hermetic tests

I'll create a concise structural summary.
</think>

## URL Deduplication Tests

**Source:** `tests/test_f_a5_url_dedup.py` — Hermetic test suite validating URL dedup behavior with bloom filter integration.

### Test Organization (13 tests across 5 sections)

- **Basic correctness** (5 tests): Verify first-seen-wins ordering, correct unique/dropped counts, normalize flag collapsing scheme/host
- **Filter mutation contract** (2 tests): Assert filter is mutated after dedupe
- **Edge cases** (3 tests): Unparseable URLs kept in output but NOT added to filter (poisoning prevention), empty strings dropped
- **None filter fallback** (1 test): In-list dedup without mutation
- **Real-world scenario** (1 test): 1800 raw URLs → 600 unique across 3 query batches

### Key API Contract

- `dedupe_url_list(urls, filter=None, normalize=True)` returns `tuple[unique_urls, dropped_count]`
- `normalize=True`: lowercases scheme/host before comparison
- `normalize=False`: preserves raw strings verbatim

### Isolation Pattern

`fresh_filter` pytest fixture provides per-test `RotatingBloomFilterAdapter` instance, preventing singleton state leakage across tests.

### Edge Case Rules

1. Unparseable URLs remain in output but are excluded from filter
2. Empty strings count as dropped URLs
3. None filter falls back to list-only dedup without side effects

**Drill-down:** See `url_deduplication_tests.md` for full test implementations and examples.