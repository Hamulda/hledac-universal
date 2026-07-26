---
children_hash: eb3aa85b8c09019f5ac3e035ad4a97c23369c490c91960119e9f1f6db937e18a
compression_ratio: 0.9710526315789474
condensation_order: 1
covers: [url_deduplication_tests.md]
covers_token_total: 380
summary_level: d1
token_count: 369
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the provided knowledge entries. This is about URL deduplication tests. I need to summarize the key information while preserving facts, architectural decisions, and relationships, and reference the entry name for drill-down.

Let me create a concise structural summary.
</think>

## URL Deduplication Tests (url_deduplication_tests)

### Overview
Documents hermetic test suite for `dedupe_url_list` gate using `RotatingBloomFilterAdapter` from `tests/test_f_a5_url_dedup.py`.

### Test Structure
- **Fixture**: `@pytest.fixture fresh_filter` provides hermetic isolation per test
- **Flow**: fresh filter → basic correctness → filter mutation → edge cases → None filter fallback → real-world scenario

### Test Categories

**Basic Correctness**
- Empty list, single URL, intra-batch duplicates
- First-seen wins ordering preserved

**Filter Mutation Contract**
- `filter.add()` called once per surviving URL
- No re-addition of already-filtered URLs

**Edge Cases**
- Unparseable URLs: kept in output, not added to filter
- Empty strings handled
- `normalize=False` skips URL normalization

**None Filter Fallback**
- Graceful degradation when filter unavailable

**Real-World Scenario**
- 3 queries × 30 hosts × 20 pages = 1800 raw URLs
- Collapses to 600 unique URLs via deduplication

### Dependencies
- `pytest.mark.asyncio` for async tests
- `pytest.raises` for exception assertions