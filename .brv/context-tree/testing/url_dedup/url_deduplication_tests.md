---
title: URL Deduplication Tests
summary: Hermetic tests for dedupe_url_list gate with RotatingBloomFilterAdapter covering intra/cross-batch dedup, filter mutation, edge cases, and 3-query discovery scenario (1800 URLs -> 600 unique)
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:19:18.608Z'
updatedAt: '2026-07-26T11:19:18.608Z'
---
## Reason
Document URL deduplication test suite from tests/test_f_a5_url_dedup.py

## Raw Concept
**Task:**
Document URL deduplication test suite covering RotatingBloomFilterAdapter, dedupe_url_list function, and cross-batch deduplication

**Files:**
- tests/test_f_a5_url_dedup.py

**Flow:**
create fresh filter -> test basic correctness -> test filter mutation contract -> test edge cases -> test None filter fallback -> test real-world discovery scenario

**Timestamp:** 2026-07-26

## Narrative
### Structure
Tests use @pytest.fixture fresh_filter for hermetic isolation. Test categories: basic correctness (empty, single, intra-batch dups), filter mutation contract (adds surviving URLs, no re-add), edge cases (unparseable URLs, empty strings, normalize=False), None filter fallback, and 3-query discovery scenario.

### Dependencies
Requires pytest.mark.asyncio for async tests, pytest.raises for exception assertions

### Highlights
First-seen wins ordering, filter.add called once per surviving URL, unparseable URLs kept but not added to filter, normalize=False skips URL normalization, 3 queries x 30 hosts x 20 pages = 1800 raw URLs collapses to 600 unique
