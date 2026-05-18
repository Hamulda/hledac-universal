# Intelligence Adapter Boundary Audit

**Date:** 2026-05-18
**Scope:** `intelligence/temporal_archaeologist_adapter.py`, `intelligence/ti_feed_adapter.py`, `discovery/ti_feed_adapter.py`
**Author:** Vojtech Hamada

---

## 1. `TemporalArchaeologistAdapter` — MEANINGFUL

### Production Callers
| Caller | Method |
|--------|--------|
| `runtime/sprint_scheduler.py` | `_run_temporal_archaeology_sidecar()` |

### Transformation Performed
- `synthesize_timeline(ct_findings, archive_results, doc_metadata, entity_id)` — aggregates multiple source event types into a `SynthesizedTimeline` via `TimelineSynthesizer`
- `_to_derived_findings(timeline)` — converts `SynthesizedTimeline` to `CanonicalFinding` with `source_type="temporal_archaeology"` and serialized `payload_text`

### Bounds / Fail-Soft
| Bound | Value |
|-------|-------|
| `MAX_TIMELINE_EVENTS` (synthesizer) | 200 |
| `MAX_TIMELINE_FINDINGS` | 20 |
| `MAX_EVENT_AGE_DAYS` | 1825 (5 years) |

All methods wrapped in `try/except` — errors return empty result, never crash the sprint.

### Persistence / Write Behavior
Derived findings go through `async_ingest_findings_batch()` — canonical write path to DuckDB.

### Deletion Test Result
**CANNOT DELETE** — real transformation logic, production caller exists, 42 tests pass in `probe_f202e`.

**VERDICT: MEANINGFUL — retain.**

---

## 2. `intelligence.ti_feed_adapter.TIFeedAdapter` — DEPRECATED / ZERO production callers

### Production Callers
**NONE** — zero imports from `intelligence.ti_feed_adapter` found in the codebase.

### Deprecated Status
Explicitly marked deprecated at import time:
```python
warnings.warn(
    "intelligence.ti_feed_adapter je deprecated. "
    "Používej discovery.ti_feed_adapter.",
    DeprecationWarning, stacklevel=2
)
```

### What It Does (mirrors-first TI feed adapter)
- `MirrorManager` — downloads and manages local mirror files (CISA KEV, URLhaus, ThreatFox, Feodo, OpenPhish, NVD)
- `TIFeedAdapter.get_iocs(indicator, ioc_type)` — queries local mirrors first, returns findings with `tier='local_mirror'`, `priority=95`
- Supports CVE, URL, IP, malware indicators
- 5-minute in-memory cache per source
- Fail-open: HTTP errors leave stale mirror in place, pipeline continues

### Contrast: `discovery.ti_feed_adapter`
| | `intelligence.ti_feed_adapter` | `discovery.ti_feed_adapter` |
|--|---|---|
| Architecture role | Standalone mirror-first TI | OSINT task handlers |
| Transport | `aiohttp` inline | `checked_aiohttp_get/post` (circuit breaker) |
| Key functions | `MirrorManager`, `TIFeedAdapter` | `search_crtsh`, `certstream_monitor`, `github_dork`, `query_rdap`, `search_ahmia`, `scrape_pastebin_for_keyword`, `fetch_malwarebazaar_*`, `_handle_ipfs_*` |
| Production callers | **NONE** | `tool_registry.py` (OSINT handlers), `tests/test_ipfs_canonical.py` |
| Sprint integration | No | Yes (`@register_task` registry) |
| Persistence | None (read-only mirrors) | `async_ingest_findings_batch()` canonical write |

### Deletion Test Result
**SAFE TO DELETE** — no production callers, deprecated, superseded by `discovery.ti_feed_adapter`.

**VERDICT: Deprecated shim — queue for removal in future cleanup commit.**

---

## 3. `discovery.ti_feed_adapter` — ACTIVE (out of scope but documented)

Active OSINT adapter with 10+ production call sites via `tool_registry.py` `@register_task` handlers:
- `search_crtsh`, `certstream_monitor`, `github_dork`, `query_rdap`, `search_ahmia`, `scrape_pastebin_for_keyword`
- `fetch_malwarebazaar_*`, `_handle_ipfs_*`
- All protected by circuit breaker (`checked_aiohttp_get/post`)
- Writes via `async_ingest_findings_batch()` — canonical path

Not modified by this audit.

---

## Summary Matrix

| Adapter | Status | Production Callers | Transformation | Bound | Persistence |
|---------|--------|--------------------|----------------|-------|-------------|
| `intelligence.temporal_archaeologist_adapter.TemporalArchaeologistAdapter` | MEANINGFUL | 1 (`sprint_scheduler`) | Multi-source timeline synthesis → derived CanonicalFinding | `MAX_TIMELINE_EVENTS=200`, `MAX_TIMELINE_FINDINGS=20` | `async_ingest_findings_batch()` |
| `intelligence.ti_feed_adapter.TIFeedAdapter` | DEPRECATED | 0 | Mirror-first TI feed lookup (mirrors-only) | 50MB mirror cap, 5min cache | None (read-only) |
| `discovery.ti_feed_adapter.TIFeedAdapter` | ACTIVE | Many (tool_registry) | OSINT handlers + IPFS, CIRCL PDNS, crtsh, certstream, etc. | Per-handler bounds | `async_ingest_findings_batch()` |

---

## Recommended Actions

1. **Do NOT delete `intelligence.temporal_archaeologist_adapter.py`** — meaningful transformation, production caller, 42 passing tests.

2. **Queue `intelligence/ti_feed_adapter.py` for removal** in future cleanup sprint:
   ```
   chore(intelligence): remove deprecated ti_feed_adapter shim
   ```
   - ~~Remove file~~
   - ~~Remove from `intelligence/__init__.py` exports~~ (no exports)
   - ~~Verify zero remaining imports~~
   - ✅ **REMOVED** (Sprint F229) — file deleted, test-only references removed, seal test added at `tests/probe_f229/test_seal_intelligence_ti_feed_adapter.py`
   - `discovery.ti_feed_adapter` remains ACTIVE

3. **Pre-existing test failures** (7 failures in `probe_f202e` + `test_ipfs_canonical`): caused by `tool_registry.py` import error (`WebSearchArgs` not found in `tools.registry`). Unrelated to adapter audit — fix in separate sprint.

---

## Test Results

```
tests/probe_f202e/test_temporal_archaeology_timeline.py  — 42 passed, 7 failed
tests/test_ipfs_canonical.py                            — (subset of failures above)
```

Failures are pre-existing import errors in `tool_registry.py`, not adapter-related.
