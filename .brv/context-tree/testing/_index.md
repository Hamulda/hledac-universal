---
children_hash: fecec0a6540254be92e33d3f5d081d9c0f77024c947873f248287d1f9c35bc00
compression_ratio: 0.13760186263096624
condensation_order: 2
covers: [conftest/_index.md, discovery_adapter_tests/_index.md, discovery_adapters/_index.md, exit_codes/_index.md, f350m_r_regression_tests/_index.md, memory_leaks/_index.md, url_dedup/_index.md, url_dedup_tests/_index.md]
covers_token_total: 4295
summary_level: d2
token_count: 591
type: summary
---
<think>
The user is asking me to create a structural summary at the d1 level (domain level) for the `url_dedup_tests` domain. Looking at the `_index.md` metadata, this domain has:
- 1 child entry: `url_deduplication_tests.md`
- Compression ratio: 0.9146757679180887 (very high, meaning most content is being preserved)
- Token count: 536
- Summary level: d1

I need to create a concise structural summary that references the child entry for drill-down and preserves key facts, architectural decisions, and relationships while compressing to ~20-30% of the original token count (536 tokens → ~160-270 tokens).

Let me identify the key information:
1. **Domain**: url_dedup_tests
2. **Source**: tests/test_f_a5_url_dedup.py
3. **Key function**: dedupe_url_list with normalize parameter
4. **Returns**: tuple[unique_urls, dropped_count]
5. **Bloom filter**: RotatingBloomFilterAdapter for state management
6. **Isolation pattern**: fresh_filter fixture
7. **Edge case**: Unparseable URLs kept in output but NOT added to filter
8. **Test count**: 13 tests in 5 sections
9. **Normalization**: lowercases scheme/host when normalize=True

Let me write a concise summary at d1 level.
</think>

## url_dedup_tests

**Source:** `tests/test_f_a5_url_dedup.py` — 13 tests validating URL deduplication with bloom filter integration.

### Core API

`dedupe_url_list(urls, filter=None, normalize=True)` returns `(unique_urls, dropped_count)`.

**Normalization behavior:**
- `normalize=True`: lowercases scheme/host before comparison
- `normalize=False`: preserves raw strings verbatim

### Test Structure (5 sections)

| Section | Tests | Focus |
|---------|-------|-------|
| Basic correctness | 5 | First-seen-wins ordering, counts, normalize flag |
| Filter mutation contract | 2 | Filter state changes after dedupe |
| Edge cases | 3 | Unparseable URLs kept in output but excluded from filter |
| None filter fallback | 1 | In-list dedup without mutation |
| Real-world scenario | 1 | 1800 raw URLs → 600 unique across batches |

### Key Design Decisions

**Unparseable URL handling:** Retained in output to avoid silent data loss; excluded from filter to prevent poisoning.

**Isolation pattern:** `fresh_filter` fixture provides per-test `RotatingBloomFilterAdapter` instance.

**Drill-down:** `url_deduplication_tests.md` contains full test implementations and examples.