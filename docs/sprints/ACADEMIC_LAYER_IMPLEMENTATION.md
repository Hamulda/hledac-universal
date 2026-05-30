# ACADEMIC_LAYER_IMPLEMENTATION.md

**Date:** 2026-05-30
**Sprint:** F259
**Status:** IMPLEMENTED

---

## Overview

Complete Academic Intelligence Layer providing access to hard-to-reach scholarly data beyond Google Scholar. Implemented as canonical adapters in `discovery/academic/`.

---

## Adapters

### 1. arXiv OAI-PMH Bulk Access (`arxiv_adapter.py`)

**Endpoint:** `http://export.arxiv.org/oai2`

**Features:**
- OAI-PMH bulk harvesting (not just search API)
- Incremental harvesting with `from` date param
- Full metadata: MSC classifications, journal refs, author ORCID
- Fallback to arXiv API for search queries

**Rate limit:** 1 req/3s (OAI-PMH), asyncio.Semaphore(3) cap
**Source type:** `arxiv_bulk`

**Key classes:**
- `ArxivAdapter` — main client with harvest/search methods
- `ArxivPaper` — structured paper result
- `search_arxiv()` — convenience function

---

### 2. Semantic Scholar S2ORC (`s2orc_adapter.py`)

**Endpoint:** `https://api.semanticscholar.org/graph/v1`

**Features:**
- S2AG (Semantic Scholar Academic Graph) API
- TLDR generation via `/paper/{id}/tldr` endpoint (free, 100rps)
- 2-hop citation graph traversal: seed → citing → cited
- Rate limit: 100 req/s unauthenticated → Semaphore(10) safe cap

**Source type:** `s2orc`

**Key classes:**
- `S2ORCAdapter` — main client
- `S2Paper` — paper with citations, TLDR
- `CitationEdge` — citation graph edge
- `search_s2orc()` — convenience with citation traversal

**Citation traversal:**
```
Hop 1: From seeds, max 5 papers per seed
Hop 2: From hop1[:5], max 3 papers per paper
Total cap: 50 papers
```

---

### 3. OpenAlex (`openalex_adapter.py`)

**Endpoint:** `https://api.openalex.org`

**Features:**
- Concept-based search: topic → related works by concept hierarchy
- Institution network: author/institution → collaboration network
- `filter=concepts.id:` for sub-field specific searches
- Polite pool: `mailto=` param for 10 req/s (vs 5 without)

**Source type:** `openalex`

**Key classes:**
- `OpenAlexAdapter` — main client
- `OpenAlexWork` — work with concepts, citation count
- `OpenAlexInstitution` — institution with co-authors
- `InstitutionNetwork` — collaboration network result
- `FIELD_CONCEPTS` — known concept IDs for common fields

**Pre-defined concept IDs:**
```python
FIELD_CONCEPTS = {
    "cs": "C164176025",      # Computer Science
    "ai": "C39432361",       # Artificial Intelligence
    "ml": "C185592260",      # Machine Learning
    "security": "C162324750", # Computer Security
    "crypto": "C2777199784",  # Cryptography
}
```

---

### 4. CORE.ac.uk (`core_adapter.py`)

**Endpoint:** `https://api.core.ac.uk/v3`

**Features:**
- CORE aggregates 200M+ open access papers from 10,000+ repositories
- **Full-text search over ACTUAL PAPER CONTENT** (unique feature)
- Requires free API key (set `CORE_API_KEY` in .env)
- Highlight passages with context

**Source type:** `core_fulltext`

**Key classes:**
- `COREAdapter` — main client
- `COREWork` — work with fulltext highlight
- `COREPageResult` — passage with score
- `search_core_fulltext()` — requires API key

**Env requirements:**
```
CORE_API_KEY=your_free_api_key
```

Get free key at: https://core.ac.uk/join/developer

---

### 5. Unpaywall (`unpaywall_adapter.py`)

**Endpoint:** `https://api.unpaywall.org/v2`

**Features:**
- "Last mile" resolver: DOI → free legal PDF
- 24-hour cache (DOIs don't change)
- Parallel bulk DOI resolution
- Polite pool via email param

**Source type:** `unpaywall`

**Key classes:**
- `UnpaywallAdapter` — main client
- `OAPaper` — open access paper with PDF URL
- `resolve_doi()` — single DOI lookup
- `find_free_pdf()` — convenience for PDF URL only

---

## Architecture

```
discovery/academic/
├── __init__.py          # Lazy exports, ACADEMIC_ENABLED gate
├── arxiv_adapter.py      # OAI-PMH bulk + search API
├── s2orc_adapter.py      # S2AG + citation graph
├── openalex_adapter.py  # Concept/institution search
├── core_adapter.py      # Full-text (requires API key)
└── unpaywall_adapter.py  # DOI → PDF resolver
```

---

## Integration

### Pipeline Wiring (`live_public_pipeline.py`)

Academic lane triggered when:
1. `HLEDAC_ENABLE_ACADEMIC=1` env var set
2. Query contains academic keywords (paper, research, scholar, etc.)
3. `HLEDAC_DEEP_RESEARCH=1` env var set

**Code path:**
```python
# F259: Academic research lane via discovery/academic adapters
if academic_enabled or has_academic_keywords or deep_research:
    from hledac.universal.discovery.academic import search_all_academic
    academic_results = await search_all_academic(query, max_results_per_source=10)
    await store.async_ingest_findings_batch(all_findings)
```

### Env Gates

| Variable | Effect |
|----------|--------|
| `HLEDAC_ENABLE_ACADEMIC=1` | Enable academic lane unconditionally |
| `HLEDAC_DEEP_RESEARCH=1` | Force academic lane (deep research mode) |
| `CORE_API_KEY=xxx` | Enable CORE.ac.uk full-text search |
| `HLEDAC_CONTACT_EMAIL=xxx` | Polite pool email for OpenAlex/Unpaywall |

### Academic Keywords (auto-trigger)

```python
academic_keywords = [
    "paper", "research", "academic", "scholar", "study",
    "journal", "citation", "doi", "arxiv", "publication",
    "conference", "thesis"
]
```

---

## M1 8GB Constraints

All adapters follow these invariants:

| Constraint | Value |
|------------|-------|
| Max concurrent per adapter | 3 |
| Semaphore for adapter calls | 5 max total |
| Timeout per request | 25-30s |
| Cache TTL | 15-30 min |
| Fail-soft | All HTTP errors caught |
| Bounded results | 10-20 per source |

---

## Source Types

| Adapter | source_type | Confidence |
|---------|-------------|------------|
| arXiv | `arxiv_bulk` | 0.8 |
| S2ORC | `s2orc` | 0.85 |
| OpenAlex | `openalex` | 0.8 |
| CORE | `core_fulltext` | 0.85 |
| Unpaywall | `unpaywall` | 0.9 |

---

## Canonical Finding Structure

All adapters return `CanonicalFinding` with:
- `finding_id`: SHA256 hash of query+id+source
- `source_type`: adapter-specific (see table above)
- `confidence`: 0.8-0.9 (high for verified academic sources)
- `provenance`: (source, id, title[:50])
- `payload_text`: structured metadata (title, authors, DOI, etc.)

---

## Usage Examples

### Direct adapter usage
```python
from hledac.universal.discovery.academic import search_arxiv

# Single source
findings = await search_arxiv("machine learning", max_results=20)

# Full search
from hledac.universal.discovery.academic import search_all_academic
results = await search_all_academic("transformer architecture", max_results_per_source=15)
```

### Citation traversal
```python
from hledac.universal.discovery.academic import traverse_academic_citations

traversed = await traverse_academic_citations(
    seed_dois=["10.48550/arXiv.1706.03762"],
    max_hops=2
)
```

### DOI → PDF resolver
```python
from hledac.universal.discovery.academic import find_free_pdf

pdf_url = await find_free_pdf("10.1038/nature12373")
```

---

## Verification

```bash
# Check adapter imports
python -c "from hledac.universal.discovery.academic import *; print('OK')"

# Check env gate
python -c "from hledac.universal.discovery.academic import ACADEMIC_ENABLED; print(f'Enabled: {ACADEMIC_ENABLED}')"
```

---

## Related Documentation

- `ACADEMIC_INTELLIGENCE_REPORT.md` — planned sources audit
- `ACADEMIC_SOURCES_COMPLETE.md` — previous implementation report