# Evidence Lifecycle Domain

## Metadata

- **Entry Path:** domains/evidence-lifecycle
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** domain

## Summary

Complete evidence management from collection through enrichment to export.

## Phases

| Phase | Components | Output |
|-------|-----------|--------|
| Collection | FetchCoordinator, StealthBrowser | Raw content |
| Extraction | IoCProcessor, EntityExtractor | Structured entities |
| Enrichment | ForensicsService, NER | Enhanced metadata |
| Storage | DuckDBShadowStore, LMDB | Canonical records |
| Analysis | HypothesisGraph, RAG | Correlations |
| Export | ReportEngine, STIX2 | Reports |

## Storage Locations

| Data Type | Storage |
|-----------|---------|
| Findings | DuckDB canonical_findings |
| Entity metadata | LMDB |
| RAG embeddings | LanceDB |
| Whisper cache | LMDB |
| Evidence logs | evidence/ |
