# F226B: PUBLIC Evidence Acceptance Uplift — Sprint Completion Report

**Date:** 2026-05-08
**Sprint:** F226B
**Status:** COMPLETE ✅

## Summary

Improved PUBLIC lane conversion from safe public candidates into accepted CanonicalFinding evidence by using existing parser/quality paths. No new crawler, no stealth, no browser. Added `_build_public_finding()` helper for content-only pages with zero pattern matches that pass the quality gate.

## Changes

### `pipeline/live_public_pipeline.py`

**New constant** (line ~55):
- `_PUBLIC_SOURCE_TYPE = "public"` — source_type value for public-surface findings

**New helper function** (line ~908):
- `async def _build_public_finding(...)` — builds CanonicalFinding from content-only pages with:
  - source_type = "public", label = "public_surface", confidence = 0.55
  - provenance: `source_family:public`, `url:{url}`, `label:public_surface`, `score:{discovery_score}`, `reason:{discovery_reason}`
  - payload_text: title[:200] + snippet[:300] + body[:500], hard-capped at 2000 chars
  - bounded deterministic finding_id via `_make_finding_id(query, url, "public_surface", "content_only", payload_text[:100])`

**PipelinePageResult** (line ~353):
- Added `public_surface_dup: bool = False` field for per-page duplicate signal

**PipelineRunResult** (line ~496):
- Added `public_build_success_count: int = 0` — public_surface findings built (pattern-miss pages)
- Added `public_build_failure_count: int = 0` — public_surface build attempts that returned empty
- Added `public_duplicate_count: int = 0` — public_surface findings rejected as duplicate
- Added `public_acceptance_ratio: float = 0.0` — `success / (success + failure)`

**Zero-match branch** (line ~1410):
- When `matched_count == 0`, page no longer immediately rejected — checks if content-only public finding should be built
- Condition: `extracted_text and quality_reason is not None and not quality_reason.startswith("SKIP_WEAK")`
- Quality gate NOT bypassed — SKIP_WEAK pages still return empty and fall through to rejection

**Telemetry tracking** (line ~1462):
- `_pub_build_success_count` incremented when `_pub_accepted > 0`
- `_pub_build_failure_count` incremented when public finding attempt was made but didn't produce accepted finding
- `_pub_dup_found` boolean tracks duplicate detection (stored but not accepted)

**Aggregation section** (line ~3107):
- Computes `public_build_success_count`, `public_build_failure_count`, `public_duplicate_count` from per-page telemetry
- Computes `public_acceptance_ratio = success / max(success + failure, 1)`

## Key Design Decisions

1. **Quality gate NOT bypassed**: SKIP_WEAK pages still return empty — the call-site condition `not quality_reason.startswith("SKIP_WEAK")` guards `_build_public_finding()`.

2. **Terminality separate from evidence**: pages accepted via `public_surface` are marked with `rejection_reason=None, terminal_reason=None` (accepted state), not a new terminal reason.

3. **Dedup at storage layer**: duplicate detection (`_pub_dup_found`) is based on finding_id already existing in storage — `_pub_stored > 0 but _pub_accepted == 0`.

4. **Confidence 0.55**: lower than pattern-matched (0.8) — corroborating signal, not primary evidence.

## Tests

**12 passed** (probe_f226b_public_acceptance):
- `test_creates_canonical_finding_from_content_only_page`
- `test_empty_page_text_returns_empty_tuple`
- `test_provenance_contains_url_and_label`
- `test_public_surface_label_in_finding_provenance`
- `test_public_finding_has_lower_confidence_than_pattern`
- `test_skip_weak_page_returns_empty`
- `test_ratio_is_zero_when_no_build_attempts`
- `test_ratio_calculation`
- `test_no_network_in_build_public_finding`
- `test_public_fetcher_not_called_in_probe_tests`
- `test_duplicate_detection_from_same_url`
- `test_source_type_constant_defined`

## Verification

```bash
python -m pytest tests/probe_f226b_public_acceptance/ -v
# 12 passed

# Cross-lane smoke
python -m pytest tests/probe_f226c_ct_acceptance/ tests/probe_f226a_public_surface/ -q
```

## Artifacts

- `REPORT_PUBLIC_ACCEPTANCE.md` — This report
- `public_acceptance.json` — Machine-readable sprint metadata