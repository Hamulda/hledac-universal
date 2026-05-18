# HTML Parser Dependency Audit — BeautifulSoup4 → selectolax

**Date:** 2026-05-18
**Status:** Phase 1 — Audit + Characterization Tests
**Goal:** Reduce default dependency weight; speed up HTML parsing on M1

---

## Background

| Library | Speed (M1) | Parser Type | ARM64 Native |
|---------|------------|-------------|--------------|
| selectolax | ~10-50× faster | Rust (lol_html) | ✅ Yes |
| BeautifulSoup4 + html.parser | baseline | Pure Python | ✅ Yes |
| BeautifulSoup4 + lxml | ~5-10× faster than html.parser | C extension | ⚠️ needs lxml |

selectolax lives in `osint-html` extra. beautifulsoup4 is a default dependency.

**Key insight from prior audit (F214P):** Most bs4 sites use `html.parser` (pure Python, slow) — not `lxml` (C extension). Even lxml is 5-10× slower than selectolax.

---

## File × Role Matrix

| File | Primary Parser | Fallback | Complex CSS? | Malformed HTML Tolerance | Fallback Needed? | Tests? |
|------|---------------|----------|-------------|--------------------------|-----------------|--------|
| `discovery/rss_atom_adapter.py` | selectolax ✅ | bs4 | No (feed extraction) | Medium | ✅ intentional chain | ❌ |
| `tools/content_miner.py` | selectolax ✅ | — | Yes (CSS link selectors) | High (lol_html) | ❌ | ❌ |
| `utils/html_text_fast.py` | selectolax ✅ | regex | No (text extraction) | High (lol_html) | ✅ intentional | ❌ |
| `layers/content_layer.py` | html_text_fast → bs4 | — | No | High | ✅ existing chain | ❌ |
| `deep_research/utils.py` | bs4 ⚠️ | — | No | High | ❌ | ❌ |
| `intelligence/dark_web_intelligence.py` | bs4 (lxml) ⚠️ | — | No | High | ❌ | ❌ |
| `intelligence/archive_discovery.py` | bs4 ⚠️ | — | No | High | ❌ | ❌ |
| `discovery/ti_feed_adapter.py` | bs4 ⚠️ | — | No | Medium | ❌ | ❌ |
| `tools/content_extractor.py` | bs4 opt ⚠️ | regex | No | Medium | ✅ existing chain | ❌ |

**Legend:**
- ✅ selectolax-primary (fast, Rust)
- ⚠️ bs4-primary (slow Python parser or C extension lxml)
- selectolax in `osint-html` extra; bs4 in default deps

---

## Migration Priority

### Tier 1 — Immediate candidates (selectolax already dominant or easy wins)

| File | Current | Recommended Change | Risk |
|------|---------|-------------------|------|
| `tools/content_miner.py` | selectolax-only | Already optimal | None |
| `utils/html_text_fast.py` | selectolax-first | Already optimal | None |
| `discovery/rss_atom_adapter.py` | selectolax + bs4 fallback | Already has selectolax-first; keep bs4 as intentional fallback | Low |
| `intelligence/pastebin_monitor.py` | selectolax | Already optimal | None |
| `intelligence/open_source_collectors.py` | selectolax | Already optimal | None |
| `fetching/public_fetcher.py` | selectolax-first | Already optimal (F214OPT-A) | None |

### Tier 2 — bs4-primary, low-risk migration

| File | Current Parser | Recommended | CSS Selector Support | Notes |
|------|--------------|-------------|---------------------|-------|
| `tools/content_extractor.py` | bs4 opt (html.parser) | selectolax-first, regex fallback | Limited | Already has opt-in bs4; `html.parser` is slowest |
| `discovery/ti_feed_adapter.py` | bs4 (html.parser) ×3 | selectolax-first, bs4 fallback | Limited | 3 identical bs4 patterns; consolidate + migrate |
| `intelligence/archive_discovery.py` | bs4 (html.parser) | selectolax-first, bs4 fallback | Limited | 2 sites, identical pattern |

### Tier 3 — lxml already fast; defer

| File | Current Parser | Recommendation |
|------|--------------|----------------|
| `intelligence/dark_web_intelligence.py` | bs4 (lxml) — C extension | Defer. lxml is ~5-10× faster than html.parser; marginal gain vs migration cost. |

### Tier 4 — Non-parsing bs4 uses

| File | Role | Recommendation |
|------|------|---------------|
| `deep_research/utils.py` | Table extraction | Audit scope only; not a pure HTML parser |

---

## Fallback Chain Design (Migration Target)

```
selectolax (osint-html extra)
    ↓ (if unavailable or fails)
BeautifulSoup4 + lxml (legacy-html extra)
    ↓ (if lxml unavailable or fails)
regex-stripped html.parser (stdlib, no extra)
```

**Implementation principle:** `try: selectolax except ImportError: bs4_fallback`

---

## Phase 1 Deliverables (This Commit)

- [x] This audit document
- [ ] Characterization tests for: RSS/Atom adapter, content_extractor, content_miner

---

## Open Questions

1. `deep_research/utils.py` table extraction — does selectolax have equivalent table parsing API?
2. `dark_web_intelligence.py` uses `lxml` C extension — is migration to selectolax worth the test rewrite?
3. Should bs4 move to `legacy-html` extra alongside `osint-html`?

---

## References

- Sprint F214OPT-A: selectolax-first HTML→text extraction in public_fetcher
- Sprint 33: selectolax for secure/fast link extraction in content_miner
- F214P_PROCESSPOOL_M1_AUDIT: selectolax ARM64 native performance
- F214_TRANSPORT_BROWSER_DEEP_AUDIT_2026-05-06: bs4 vs selectolax benchmark (0.2ms vs 0.5ms/page)