"""
Sprint F202I: Multimodal Evidence Triage — Probe Tests
======================================================

Invariant mapping:
  F202I-1 | EvidenceTriageCoordinator initializes metadata extractor lazily
  F202I-2 | extract_triage_facets returns TriageFacets (bounded, fail-safe)
  F202I-3 | URL/domain extraction bounded at MAX_URL_HITS=20
  F202I-4 | OCR snippets bounded at MAX_OCR_SNIPPETS=10
  F202I-5 | File hashes extracted from GenericMetadata (md5, sha256, sha1)
  F202I-6 | TriageFacets.to_dict() includes title, author, exif, gps, ocr_snippets, file_hashes, embedded_urls, embedded_domains
  F202I-7 | DocumentExtractor.extract() calls triage and builds evidence envelope
  F202I-8 | _build_document_envelope produces JSON with triage facets
  F202I-9 | Envelope bounded at _MAX_ENVELOPE_SIZE=4098
  F202I-10 | _run_evidence_triage_sidecar counts document findings with triage
  F202I-11 | SprintSchedulerResult.evidence_triage_findings_count field exists
  F202I-12 | _evidence_triage_adapter field exists in SprintScheduler
  F202I-13 | Sidecar is called after F202E temporal archaeology sidecar
  F202I-14 | Fail-soft: all errors in triage coordinator are caught
  F202I-15 | No VLM called in triage path (VisionOCR only)
  F202I-16 | RAM guard in EvidenceTriageCoordinator blocks triage when UMA tight
  F202I-17 | Size guard: files > 100MB are skipped
  F202I-18 | OCR timeout: OCR fails gracefully after OCR_TIMEOUT_S=30s
  F202I-19 | Metadata timeout: extraction fails gracefully after METADATA_TIMEOUT_S=30s
  F202I-20 | SprintScheduler sidecar chain preserved (no live_feed tuple change)
"""
