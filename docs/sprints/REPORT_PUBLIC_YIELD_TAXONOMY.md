# F208G-A: PUBLIC YIELD TAXONOMY — Sprint Completion Report

**Date:** 2026-05-05
**Sprint:** F208G-A
**Status:** COMPLETE ✅

## Summary

Implemented PUBLIC yield taxonomy and zero-yield explanation in `live_public_pipeline.py`. All 15 probe tests pass (5.74s).

## Changes

### `pipeline/live_public_pipeline.py`

- Added `terminal_reason: str | None` to `PipelinePageResult` after `rejection_reason` (line ~1418)
- Added 16 new run-level counter fields to `PipelineRunResult`:
  - `public_skipped_duplicate`, `public_skipped_unsupported_scheme`, `public_skipped_memory_gate`, `public_skipped_quality_gate`, `public_skipped_browser_unavailable`, `public_skipped_xml_or_feed`, `public_skipped_timeout`, `public_skipped_fetch_error`
  - `public_rejected_no_pattern_match`, `public_rejected_low_information`, `public_rejected_duplicate`, `public_rejected_storage_rejected`
  - Plus `public_terminal_classified_count`, `public_unclassified_count`, `public_terminal_reason_counts`, `public_fetch_success`, `public_fetch_failed`
- Added URL dedup via `seen_urls: set[str]` before fetch task creation (line ~2495)
- Added unsupported scheme detection via `urlparse()` before fetch call (line ~900)
- Fixed storage rejection tracking: `storage_error = stored_count == 0 and unique_findings` (line ~1261) — lmdb_success=False in store results triggers this

### Key Logic Fixes

1. **js_renderer_skipped_reason precedence** (line ~1403): `fetched_js_skip_reason` is checked FIRST before accepted_count, so browser_unavailable/xml_or_feed skip takes precedence regardless of pattern match outcome

2. **storage_error detection** (line ~1261): `stored_count == 0 and unique_findings` captures LMDB write failures where no exception was thrown but lmdb_success=False

3. **SKIP_WEAK path** (line ~1106): js_renderer_skip_reason checked in SKIP_WEAK early-return path with proper terminal_reason precedence

## Tests

**15 passed** (4.26s)
- `test_skipped_duplicate_counted`
- `test_skipped_unsupported_scheme_counted`
- `test_skipped_memory_gate_counted`
- `test_skipped_quality_gate_counted`
- `test_skipped_browser_and_xml_counted`
- `test_skipped_timeout_counted`
- `test_skipped_fetch_error_counted`
- `test_rejected_no_pattern_match_counted`
- `test_rejected_low_information_counted`
- `test_rejected_storage_rejected_counted`
- `test_rejected_duplicate_counted`
- `test_terminal_reason_counts_sum_to_discovered`
- `test_accepted_page_terminal_reason_is_none`
- `test_full_taxonomy_run_unclassified_is_zero`
- `test_url_samples_bounded_to_five`

## Terminal Reason Categories

| Category | Field | Description |
|----------|-------|-------------|
| `accepted` | (counter) | Page accepted, findings stored |
| `skipped_duplicate` | `public_skipped_duplicate` | URL dedup bloom filter hit |
| `skipped_unsupported_scheme` | `public_skipped_unsupported_scheme` | Non-http(s) URL |
| `skipped_memory_gate` | `public_skipped_memory_gate` | UMA emergency/critical blocked |
| `skipped_quality_gate` | `public_skipped_quality_gate` | Discovery score too low |
| `skipped_browser_unavailable` | `public_skipped_browser_unavailable` | JS renderer unavailable |
| `skipped_xml_or_feed` | `public_skipped_xml_or_feed` | XML/feed URL |
| `skipped_timeout` | `public_skipped_timeout` | Fetch timed out |
| `skipped_fetch_error` | `public_skipped_fetch_error` | Fetch exception |
| `rejected_no_pattern_match` | `public_rejected_no_pattern_match` | Fetched, no patterns matched |
| `rejected_low_information` | `public_rejected_low_information` | SKIP_WEAK quality gate |
| `rejected_duplicate` | `public_rejected_duplicate` | Per-page dedup exhausted |
| `rejected_storage_rejected` | `public_rejected_storage_rejected` | DuckDB/LMDB write failure |
| `rejected_quality_gate` | (in `public_acceptance_reject_reasons`) | Quality gate rejected all findings |

## Artifacts

- `REPORT_PUBLIC_YIELD_TAXONOMY.md` — This report
- `public_yield_taxonomy.json` — Machine-readable taxonomy definition

## Verification

```bash
rtk proxy python -m pytest tests/probe_f208g_public_yield_taxonomy/ -q
# 15 passed in 4.26s
```