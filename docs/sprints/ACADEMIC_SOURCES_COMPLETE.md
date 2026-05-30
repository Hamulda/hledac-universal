# ACADEMIC_SOURCES_COMPLETE.md

## Wave 2 Academic Sources — Implementation Report

### Source Coverage Matrix

| Source | Before | After | API Endpoint | Auth | Rate Limit |
|--------|--------|-------|---------------|------|------------|
| arXiv | ✅ | ✅ | `export.arxiv.org/api/query` | None | 1 req/3s |
| Crossref | ✅ | ✅ | `api.crossref.org/works` | None | 10 req/s |
| Semantic Scholar | ✅ | ✅ | `api.semanticscholar.org/graph/v1` | Optional | 1000 req/5min |
| **OpenAlex** | ❌ | ✅ | `api.openalex.org/works` | None (mailto) | 10 req/s |
| **IA Scholar** | ❌ | ✅ | `scholar.archive.org/search` | None | Unknown |
| **CORE.ac.uk** | ❌ | ✅ | `api.core.ac.uk/v3/search/works` | None (free tier) | 10 req/min |
| **bioRxiv** | ❌ | ✅ | `api.biorxiv.org/details/biorxiv` | None | Unknown |
| **medRxiv** | ❌ | ✅ | `api.biorxiv.org/details/medrxiv` | None | Unknown |

---

## New Functions Added

### academic_discovery.py

| Function | Signature | Returns | HTTP via |
|---------|-----------|---------|----------|
| `search_openalex` | `(query, max_results=20)` | `List[AcademicPaper]` | `async_fetch_public_text` (public_fetcher) |
| `search_ia_scholar` | `(query)` | `List[AcademicPaper]` | `async_fetch_public_text` (public_fetcher) |
| `search_core` | `(query)` | `List[AcademicPaper]` | `async_fetch_public_text` (public_fetcher) |
| `search_biorxiv` | `(query, max_results=20)` | `List[AcademicPaper]` | `async_fetch_public_text` (public_fetcher) |
| `search_medrxiv` | `(query, max_results=20)` | `List[AcademicPaper]` | `async_fetch_public_text` (public_fetcher) |
| `traverse_citation_graph` | `(seed_papers, max_hops=2)` | `List[AcademicPaper]` | `SemanticScholarClient.get_citations()` |
| `intelligence_crosslink` | `(papers)` | `Dict[str, List]` | `DataLeakHunter._check_breach_apis()` |

All new functions: `async`, bounded, fail-soft, wired into `search_academic_all()`.

### data_leak_hunter.py (IntelligenceX fix)

| Field | Before | After |
|-------|--------|-------|
| URL | `https://public.intelligencex.com/api/` | `https://2.intelx.io/intelligent/search` |
| Auth | `Authorization: Bearer {key}` | `X-Key: {key}` |
| Payload | `{"searchtype": ..., "searchquery": val, "limit": 20}` | `{"term": val, "maxResults": 10, "media": 0}` |
| Response | `data.get("results", [])` | `data.get("records", [])` |
| `target` field | `target=value` (wrong param) | `target=val` (correct) |
| `breach_name` | `result.get("source")` | `result.get("name")` |
| `category` | `result.get("category")` | `result.get("mediaName")` |

---

## Citation Traversal — 2-Hop BFS

```
seed_papers (up to 10)
    │
    ├─ Hop 1: get_citations(seed, limit=5) → hop1_papers
    │           MAX 50 papers total
    │
    └─ Hop 2: get_citations(hop1[:5], limit=3) → hop2_papers
                (only if max_hops >= 2 and under limit)
```

- `MAX_CITATION_PAPERS = 50`
- `MAX_HOPS = 2`
- DOI-based paper_id (fallback: title hash)
- Deduplication via `visited` set
- fail-soft: empty list on error

---

## Intelligence Cross-linking

After `traverse_citation_graph()`, `intelligence_crosslink()`:

1. **Email extraction** — regex `[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}` from author strings
2. **Breach check** — `DataLeakHunter._check_breach_apis(email, "email")` (lazy import)
3. **Institution extraction** — from `AcademicPaper.affiliations`
4. **Relationship discovery** — `RelationshipDiscoveryEngine.predict_hidden_connections()` (lazy import)

Returns: `{"breach_alerts": [...], "relationships": [...]}`

---

## Constants Added

```python
OPENALEX_BASE = "https://api.openalex.org/works"
IARCHIVE_SCHOLAR = "https://scholar.archive.org/search"
CORE_API = "https://api.core.ac.uk/v3/search/works"
BIORXIV_API = "https://api.biorxiv.org/details/biorxiv"
MEDRXIV_API = "https://api.biorxiv.org/details/medrxiv"
MAX_CITATION_PAPERS = 50
MAX_HOPS = 2
```

---

## Verification Results

```bash
$ python -c "from hledac.universal.intelligence.academic_discovery import (...)"
All imports OK

$ python -m py_compile intelligence/academic_discovery.py
Syntax OK

$ python -m py_compile intelligence/data_leak_hunter.py
Syntax OK
```

All 7 new functions confirmed `async=True`:
`search_openalex`, `search_ia_scholar`, `search_core`, `search_biorxiv`, `search_medrxiv`, `traverse_citation_graph`, `intelligence_crosslink`

---

## Architecture Notes

- FetchCoordinator seam: `async_fetch_public_text` from `public_fetcher` (not direct httpx/aiohttp)
- Circuit breaking via FetchCoordinator's domain penalty tracking
- Lazy imports for `DataLeakHunter` and `RelationshipDiscoveryEngine` to avoid circular deps
- `AcademicPaper` extended: `affiliations: List[str]`, `paper_id` property (DOI-based)
- All new sources wired into `search_academic_all()` via `asyncio.gather()`
- IntelligenceX: `2.intelx.io` is the correct production endpoint (not `public.intelligencex.com`)