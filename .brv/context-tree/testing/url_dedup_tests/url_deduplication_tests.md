---
title: URL Deduplication Tests
summary: 'Hermetic test suite for URL dedup: RotatingBloomFilterAdapter isolation, intra/cross-batch dedup, filter mutation contract, edge cases (unparseable URLs, None filter fallback)'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:18:32.790Z'
updatedAt: '2026-07-26T11:18:32.790Z'
---
## Reason
Document URL dedup test suite covering bloom filter integration and dedupe_url_list contract

## Raw Concept
**Task:**
Document test_f_a5_url_dedup.py test suite

**Changes:**
- Added URL dedup hermetic tests
- Tested RotatingBloomFilterAdapter isolation pattern

**Files:**
- tests/test_f_a5_url_dedup.py

**Flow:**
fresh_filter fixture -> dedupe_url_list() -> verify unique/dropped counts -> assert filter mutation

**Timestamp:** 2026-07-26

## Narrative
### Structure
test_f_a5_url_dedup.py contains 13 tests organized into sections: Basic correctness (5 tests), Filter mutation contract (2 tests), Edge cases (3 tests), None filter fallback (1 test), Real-world scenario (1 test)

### Dependencies
Requires fresh_filter pytest.fixture providing per-test RotatingBloomFilterAdapter isolation

### Highlights
Hermetic test pattern: each test gets fresh filter via fixture to avoid singleton state leakage. dedupe_url_list returns (unique_list, dropped_count). First-seen wins preserves order. Unparseable URLs kept in output but NOT added to filter. normalize=True (default) collapses HTTPS/HTTPS, normalize=False preserves raw strings.

### Rules
Rule 1: fresh_filter fixture MUST provide isolated RotatingBloomFilterAdapter per test
Rule 2: dedupe_url_list returns tuple of (unique_urls, dropped_count) where dropped_count is int
Rule 3: dedupe_url_list with normalize=True normalizes URLs before dedup (lowercases scheme/host)
Rule 4: Unparseable URLs are KEPT in output but NOT added to filter (prevents poisoning)
Rule 5: dedupe_url_list with None filter falls back to in-list dedup without mutation
Rule 6: Empty strings count as dropped URLs

### Examples
test_dedupe_discovery_scenario_150_urls_from_3_queries: 1800 raw URLs -> 600 unique (3x30x20 input, 30x20 unique)
test_dedupe_cross_batch_dups_dropped: filter pre-seeded with URL, second dedupe pass recognizes it as seen
test_dedupe_unparseable_urls_kept_without_poisoning_filter: garbage URLs stay in result but do not enter filter
