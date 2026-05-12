# FOCA Integration Status - Sprint FOCADI-16

## Summary
FOCA metadata extraction pipeline Phase 1-3 implementation complete. Wired into canonical pipeline.

---

## What Was Connected

### 1. EvidenceTriageCoordinator Wiring (`multimodal/evidence_triage.py`)

**TriageFacets.metadata field added** (line 123):
```python
metadata: dict[str, Any] = field(default_factory=dict)
```

**PPTX/ODP → TriageFacets.metadata** (lines 422-435):
- `company` → `facets.metadata["company"]`
- `template_path` → `facets.metadata["template_path"]`
- `slide_count` → `facets.metadata["slide_count"]`
- `speaker_notes[:3]` → `facets.metadata["speaker_notes"]`
- `hidden_slides_count` → `facets.metadata["hidden_slides_count"]`
- `has_macros` → `facets.metadata["has_macros"]`

**Email (EML/MSG) → TriageFacets.metadata** (lines 437-448):
- `from_addr` → `facets.metadata["from_addr"]`
- `reply_to` → `facets.metadata["reply_to"]`
- `message_id_domain` → `facets.metadata["message_id_domain"]`
- `originating_ip` → `facets.metadata["originating_ip"]`
- `received_chain[:3]` → `facets.metadata["received_chain"]`
- `attachment_count` → `facets.metadata["attachment_count"]`

**CAD (SVG/DXF) → TriageFacets.metadata** (lines 450-459):
- `cad_version` → `facets.metadata["cad_version"]`
- `viewbox` → `facets.metadata["viewbox"]`
- `dimensions` → `facets.metadata["dimensions"]`

### 2. FOCA Bounds Enforced (`forensics/metadata_extractor.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_SPEAKER_NOTES` | 50 | Speaker notes per presentation |
| `MAX_HIDDEN_SLIDES` | 100 | Hidden slides per presentation |
| `MAX_EMBEDDED_FONTS` | 100 | Embedded fonts per presentation |
| `MAX_INTERNAL_PATHS` | 500 | Internal paths per presentation |
| `MAX_RECEIVED_HEADERS` | 20 | Email Received headers |
| `MAX_EMAIL_HEADERS` | 200 | Email headers total |
| `MAX_MACRO_URLS` | 50 | C2 URLs extracted from macros |

### 3. Macro C2 URL Extraction (`forensics/metadata_extractor.py`)

**`_extract_macro_urls()` function** (lines 63-100):
- **olevba integration**: If `olevba` available, uses `VBALogicalLinesExtractor` for proper VBA parsing
- **Fallback**: Raw ZIP/bytes scanning with `rb"https?://[^\s<>'\"]+"` regex when olevba not available
- **Bounds**: Respects `MAX_MACRO_URLS=50`

**PPTXMetadata.macro_urls field** (line 395):
```python
macro_urls: List[str] = field(default_factory=list)
```

### 4. UniversalMetadataExtractor Routing (`forensics/metadata_extractor.py`)

**File type routing** (lines 1071-1090):
- `.pptx`, `.odp` → `_extract_pptx_metadata()` → `PPTXMetadata`
- `.svg` → `_extract_svg_metadata()` → `CADMetadata`
- `.dxf` → `_extract_dxf_metadata()` → `CADMetadata`
- `.eml`, `.msg` → `_extract_email_metadata()` → `EmailMetadata`

---

## What Remains Isolated (by design)

### DocumentIntelligenceEngine (`intelligence/document_intelligence.py`)

**Reason**: This module has its own ZIP parsing (`_analyze_ooxml`) which extracts different data (embedded objects, comments, hyperlinks). It does NOT use `UniversalMetadataExtractor`.

**Isolation is intentional** because:
- `OfficeDocumentAnalyzer` operates on raw bytes/content, not file paths
- It already extracts author, company, title via `_extract_ooxml_core_props()`
- It handles DOCX/XLSX/PPTX uniformly via ZIP parsing
- FOCA types (PPTXMetadata, etc.) are for forensics, not document intelligence

**However**: If you need FOCA PPTX data in document_intelligence, you can call `UniversalMetadataExtractor.extract()` separately and merge results.

### Legacy Path (`legacy/autonomous_orchestrator.py`)

The legacy orchestrator has its own `MetadataExtractor` class (line 22243) which is different from `UniversalMetadataExtractor`. This is legacy code not currently used by the canonical pipeline.

---

## Phase 3 Complete - What's Done

| Feature | Status | Location |
|---------|--------|----------|
| PPTX/ODP metadata extraction | Done | `metadata_extractor.py:2088` |
| Email header forensics | Done | `metadata_extractor.py:2253` |
| CAD/SVG/DXF metadata | Done | `metadata_extractor.py:2112, 2160` |
| TriageFacets wiring | Done | `evidence_triage.py:422-459` |
| Macro URL extraction (olevba) | Done | `metadata_extractor.py:63-100` |
| Macro URL fallback (ZIP scan) | Done | `metadata_extractor.py:63-100` |
| Bounds enforcement | Done | All collections bounded |

---

## Phase 4 - FOCA Integration Step 3 Complete ✓

### DocumentIntelligenceEngine FOCA Seam

**`OfficeDocumentAnalyzer`** (intelligence/document_intelligence.py):

1. **New `analyze_async()` method** (line 640):
   - Async wrapper that calls `_analyze_ooxml_async()` for FOCA enrichment
   - Falls back to sync `_analyze_ole()` for OLE formats

2. **New `_analyze_ooxml_async()` method** (line 655):
   - Base analysis via ZIP parsing (`_analyze_ooxml`)
   - Then calls `UniversalMetadataExtractor.extract()` for FOCA metadata
   - Merges FOCA results into `metadata.raw_metadata['foca']`

3. **New `_merge_foca_metadata()` method** (line 673):
   - Extracts `pptx`, `email`, `cad` from `MetadataResult`
   - Stores in `DocumentAnalysis.metadata.raw_metadata['foca']`
   - Different seam from `TriageFacets.metadata` (per requirements)

4. **FOCA extractor lazy initialization** (line 607):
   - `__init__` initializes `_foca_extractor=None`, `_foca_initialized=False`
   - `_get_foca_extractor()` does lazy import + `await initialize()`
   - M1-safe: no blocking import at class construction

### Confidence Scoring Integration

**`ForensicsEnricher._score_foca_findings()`** (enrichment_service.py:476):

Scores FOCA metadata for confidence pipeline integration:
- PPTX: `macro_urls` (+0.1), `has_macros` (+0.05), `hidden_slides` (+0.05), `template_path` (+0.05)
- Email: `originating_ip` (+0.1), `dkim_domain|spf_result` (+0.05), `attachment_count` (+0.05)
- CAD: `autocad_version` (+0.1), `coordinate_extents` (+0.05)
- Capped at 0.3 to avoid over-weighting

### Test Coverage

**`tests/test_foca_integration.py`** — 15 tests now include:
- `TestFOCADocumentIntelligenceSeam` (4 tests): async analyze, sync fallback, FOCA merge, graceful degradation
- `TestFOCAConfidenceScoring` (6 tests): score computation, PPTX/Email/CAD signals, cap enforcement

**41 tests pass** (FOCA + forensics enrichment)

---

## Phase 4 - Pending Items

1. **Entity extraction bridge** - `EmailMetadata.x_originating_ip` → NetworkIntelligence lookup
   - Not implemented: requires understanding of enrichment queue system

---

## Files Modified

| File | Changes |
|------|---------|
| `forensics/metadata_extractor.py` | +280 lines: FOCA classes, bounds, macro extraction |
| `multimodal/evidence_triage.py` | +45 lines: TriageFacets.metadata, FOCA wiring |
| `intelligence/document_intelligence.py` | +75 lines: OfficeDocumentAnalyzer FOCA seam |
| `forensics/enrichment_service.py` | +40 lines: _score_foca_findings() |
| `tests/test_foca_integration.py` | +120 lines: DI seam + confidence scoring tests |
| `FOCA_INTEGRATION_STATUS.md` | Step 3 documentation |

---

## Invariants Maintained

- **GHOST_INVARIANTS**: All extraction wrapped in try/except, fail-safe
- **Timeout guards**: `METADATA_TIMEOUT_S` respected in `_extract_metadata()`
- **RAM guards**: `_check_ram_guard()` called before extraction
- **M1 8GB**: No new heavy dependencies, all collections bounded
- **Optional dependencies**: olevba wrapped in try/except ImportError
