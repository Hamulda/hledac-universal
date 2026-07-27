---
children_hash: 31dc87237acd0f1a7d7db55591db4c0e7e693b6db10379d0f4ef5433b9a048fd
compression_ratio: 0.09643788010425716
condensation_order: 3
covers: [architecture/_index.md, data/_index.md, facts/_index.md, knowledge_base/_index.md, memory/_index.md, testing/_index.md]
covers_token_total: 5755
summary_level: d3
token_count: 555
type: summary
---
<think>
The user wants me to create a structural summary at level d2 (topic level) from the child entry provided. I need to synthesize this into a concise domain-level overview that references entry names for drill-down while preserving key facts, architectural decisions, and relationships.

Let me analyze the child entry:

**url_deduplication_tests.md** - A test suite for URL deduplication with:
- 13 tests in 5 sections
- Tests RotatingBloomFilterAdapter isolation
- dedupe_url_list returns (unique_list, dropped_count)
- Edge cases like unparseable URLs, None filter fallback
- Fresh filter fixture pattern for hermetic tests

I need to create a clean, well-structured summary that:
1. Has a clear overview
2. References the child entry for drill-down
3. Preserves key facts and patterns
4. Achieves ~20-30% compression
</think>

## URL Deduplication Tests

**Source:** `tests/test_f_a5_url_dedup.py` — Hermetic test suite validating URL dedup behavior with bloom filter integration.

### Test Organization (13 tests across 5 sections)

- **Basic correctness** (5 tests): First-seen-wins ordering, correct unique/dropped counts, normalize flag collapsing scheme/host
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