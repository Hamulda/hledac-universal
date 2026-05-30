# ACADEMIC_INTELLIGENCE_REPORT.md
**Date:** 2026-05-23
**Scope:** `hledac/universal/intelligence/academic_discovery.py` + `data_leak_hunter.py`
**Status:** AUDIT COMPLETE

---

## 1. Academic Discovery — Source Coverage Matrix

| Source | Present | API Endpoint | Circuit Broken | Rate Limited |
|--------|---------|--------------|----------------|--------------|
| arXiv | ✅ | via `academic_search.AcademicSearchEngine` | ✅ | ✅ |
| CrossRef | ✅ | via AcademicSearchEngine | ✅ | ✅ |
| Semantic Scholar | ✅ | via AcademicSearchEngine | ✅ | ✅ |
| **OpenAlex** | ❌ | `https://api.openalex.org/works?search=<q>` | ❌ | ❌ |
| **CORE.ac.uk** | ❌ | `https://api.core.ac.uk/v3/search/works` | ❌ | ❌ |
| **Internet Archive Scholar** | ❌ | `https://scholar.archive.org/search?q=<q>` | ❌ | ❌ |
| **bioRxiv/medRxiv** | ❌ | `https://api.biorxiv.org/details/biorxiv` | ❌ | ❌ |
| **SSRN** | ❌ | (none) | ❌ | ❌ |
| **ResearchSquare** | ❌ | (none) | ❌ | ❌ |
| **Unpaywall** | ❌ | `https://api.unpaywall.org/v2/<doi>` | ❌ | ❌ |
| **OpenAccessButton** | ❌ | `https://api.openaccessbutton.org/` | ❌ | ❌ |

### Missing Capabilities (Academic)

1. **Citation network traversal** — 0 lines. No `get_citations()`, no 2-hop traversal, no citation graph.
2. **Preprint servers** — bioRxiv, medRxiv, SSRN, ResearchSquare not queried.
3. **ForensicsCoordinator wiring** — No link to `ForensicsCoordinator` for PDF metadata extraction on academic PDFs.
4. **DOI → citation graph resolution** — Only CrossRef metadata, no DOI → full-text PDF → citation chain.
5. **IdentityStitchingEngine integration** — Author affiliations not fed to entity stitching.
6. **RelationshipDiscoveryEngine integration** — Institution names not linked to threat intelligence.

---

## 2. Citation Network Traversal — Implementation Gap

**Current state:** NONE. `academic_discovery.py` returns flat `AcademicPaper` lists.

**Required capability (beyond-indexed):**
```
Paper X (found) → get_citations(X) → Paper Y (may be restricted)
                                     → get_citations(Y) → Paper Z (metadata-free)
```

**Proposed implementation:**
- `MAX_CITATION_PAPERS = 50` per sprint
- `MAX_HOPS = 2`
- `get_paper_citations(paper: AcademicPaper) -> List[AcademicPaper]`
- `traverse_citation_graph(seed_papers: List[AcademicPaper]) -> List[AcademicPaper]`
- Each hop bounded by `max_results=25` to stay within 50-paper budget
- DOI lookup via CrossRef for restricted papers (metadata always free)

---

## 3. Data Leak Hunter — API Coverage Matrix

| API / Source | Method Present | Rate Limited | Notes |
|--------------|---------------|-------------|-------|
| HaveIBeenPwned | ✅ `check_email_breaches` | ✅ 61s | Generic handler |
| LeakLookup | ✅ | ✅ 61s | |
| Dehashed | ⚠️ config only | ⚠️ 61s | Config present, no dedicated method |
| **IntelligenceX** | ⚠️ partial | ❌ | `intelligencex_id` in config, no `checkIntelligenceX` method |
| **GREP.app** | ❌ | ❌ | Not implemented |
| **PastebinMonitor** | ⚠️ rate limit 61s | ✅ | `_throttle()` + `_RATE_S = 61` confirmed |
| **GitHub Secret Scanner** | ⚠️ config | ❌ | Config present |
| Spypi | ❌ | ❌ | Not found |

---

## 4. Missing Academic Sources — Implementation Plan

### 4a. OpenAlex API (PRIORITY)
```python
OPENALEX_BASE = "https://api.openalex.org/works"
# Free, 200M+ papers, excellent citation data
# Response includes: authors, institutions, concepts, citations count
```

### 4b. Internet Archive Scholar
```python
IARCHIVE_SCHOLAR = "https://scholar.archive.org/search"
# Full-text search across archived academic papers
```

### 4c. CORE.ac.uk
```python
CORE_API = "https://api.core.ac.uk/v3/search/works"
# Aggregates 200M+ open access papers
```

### 4d. Preprint Servers (bioRxiv/medRxiv)
```python
BIORXIV_API = "https://api.biorxiv.org/details/biorxiv"
MEDRXIV_API = "https://api.medrxiv.org/details/medrxiv"
```

All must use `FetchCoordinator` (not direct httpx/curl_cffi) for circuit breaking.

---

## 5. Intelligence Cross-Link Design

For each academic finding, extract and route:

| Field | Destination | Purpose |
|-------|-------------|---------|
| `doi` | DOI resolver → citation graph | 2-hop citation traversal |
| `authors[].affiliation` | IdentityStitchingEngine | Link academic authors to IOCs |
| `institution names` | RelationshipDiscoveryEngine | Link universities/orgs to threat actors |
| `emails` (if present) | DataLeakHunter | Check for credential breaches |

---

## 6. DataLeakHunter Deficiencies

**Critical:**
- `checkBreachApis`, `checkHaveIBeenPwned`, `checkLeakLookup`, `checkDehashed`, `checkIntelligenceX` — NOT found as methods
- Actual check logic lives in `check_email_breaches()` (generic handler) and `check_target()` (dispatch)
- IntelligenceX ID supported in config but no dedicated `checkIntelligenceX` method
- GREP.app (code secret scanning beyond GitHub) — NOT implemented

**PasteMonitor rate limit:** 61s confirmed via `_RATE_S` and `_throttle()` — ✅ CORRECT

---

## 7. Recommended Actions

| Priority | Action | File |
|----------|--------|------|
| P0 | Add OpenAlex API (`search_openalex`) via FetchCoordinator | academic_discovery.py |
| P0 | Add Internet Archive Scholar (`search_iarchive_scholar`) | academic_discovery.py |
| P0 | Add `checkIntelligenceX` method to DataLeakHunter | data_leak_hunter.py |
| P1 | Add CORE.ac.uk API (`search_core`) | academic_discovery.py |
| P1 | Implement 2-hop citation traversal (`traverse_citation_graph`) | academic_discovery.py |
| P1 | Wire ForensicsCoordinator for PDF metadata on academic findings | sprint_scheduler.py |
| P1 | Add author affiliations → IdentityStitchingEngine | academic_discovery.py |
| P2 | Add bioRxiv/medRxiv preprint servers | academic_discovery.py |
| P2 | Add GREP.app secret scanning | data_leak_hunter.py |
| P2 | Add Unpaywall/OpenAccessButton for full-text PDF resolution | academic_discovery.py |