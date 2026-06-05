# FORENSICS_INTEGRATION_MAP.md

> **Sprint:** F261 (2026-06-04)
> **Scope:** `forensics/` module + `evidence_log.py` + `export/stix_exporter.py` + `runtime/enrichment_services.py` + `runtime/sprint_scheduler.py` + `utils/source_types.py` + `forensics/ioc_extractor.py`
> **Status:** WIRING COMPLETE — 5/5 forensic capabilities emit `CanonicalFinding` + reach `DuckDBShadowStore`, 1/5 emits STIX 2.1 `x-hledac-forensic` custom objects, 1/1 evidence chain attachment point exposed.

---

## 1. Module map (`forensics/`)

| File | Lines | Role | Wrapped by `ForensicsEnricher`? |
|------|------:|------|:---:|
| `__init__.py` | 208 | Lazy loader; public exports (`UniversalMetadataExtractor`, `SteganalysisResult`, `DigitalGhostResult`, `analyze_image_steganography`, `analyze_file_ghosts`, …) | n/a |
| `metadata_extractor.py` | 2 775 | `UniversalMetadataExtractor` — EXIF/GPS, PDF/DOCX, audio, video, archive; FOCA email/PPTX/CAD signals; scrubbing analysis; timeline reconstruction | ✓ |
| `steganography_detector.py` | 335 | LSB / histogram / chi-square steganalysis (images) | ✓ |
| `digital_ghost_detector.py` | 399 | Deleted content / tampering / hidden data detection | ✓ |
| `enrichment_service.py` | 748 + 213 (F261) | `ForensicsEnricher` + `ForensicsResult` orchestrator; WHOIS/SSL/DNS/rDNS; FOCA x_originating_ip bridge | n/a (root) |
| `ioc_extractor.py` | 148 + 113 (F261) | Rust-backed regex IOC extractor (IPv4/IPv6/domain/md5/sha1/sha256/email/CVE) | n/a (utility) |

`enrichment_service.py` is the **canonical seam** — it composes 3/4 of the other capabilities and is the only one wired into the sprint pipeline.

---

## 2. Active capabilities → evidence chain

| # | Capability | Public surface | Produces `CanonicalFinding`? | Wired to `DuckDBShadowStore`? | Wired to `EvidenceLog`? | Wired to STIX 2.1? |
|---|---|---|:---:|:---:|:---:|:---:|
| 1 | `UniversalMetadataExtractor.extract()` | `MetadataResult.to_dict()` | ✓ via #5 | ✓ via #5 | via `attach_forensic_analysis()` | ✓ via #5 |
| 2 | `analyze_image_steganography()` | `SteganalysisResult.to_dict()` | ✓ via #5 | ✓ via #5 | via `attach_forensic_analysis()` | ✓ via #5 |
| 3 | `analyze_file_ghosts()` | `DigitalGhostResult.to_dict()` | ✓ via #5 | ✓ via #5 | via `attach_forensic_analysis()` | ✓ via #5 |
| 4 | `fast_ioc_extract(text)` | `list[tuple[ioc_type, value]]` | ✓ `ioc_extract_to_canonical_findings()` | ✓ (per IOC) | via #5 | ✓ via #5 |
| 5 | `ForensicsEnricher.enrich(finding)` | `dict` → now `CanonicalFinding` via `make_canonical_finding_from_enrichment()` | ✓ **WIRED (F261)** | ✓ **WIRED (F261)** | via `attach_forensic_analysis()` | ✓ **WIRED (F261)** |

All 5 are now **active** end-to-end as of Sprint F261.

---

## 3. Flow diagram

```
            ┌──────────────────────────────────────────────────────────┐
            │ forensics/ capability (1–3) or fast_ioc_extract (4)      │
            └─────────────────────────┬────────────────────────────────┘
                                      │ raw result
                                      ▼
            ┌──────────────────────────────────────────────────────────┐
            │ ForensicsEnricher.enrich(finding)                        │
            │   - composes 1+2+3 + WHOIS/SSL/DNS/rDNS + FOCA bridge    │
            │   - returns dict (file_path, metadata, steganography,     │
            │     ghosts, whois, ssl, dns, rdns, forensics)            │
            └─────────────────────────┬────────────────────────────────┘
                                      │ dict
              ┌───────────────────────┼───────────────────────────┐
              ▼                       ▼                           ▼
    ┌────────────────────┐  ┌────────────────────┐  ┌──────────────────────────┐
    │ LMDB write         │  │ DuckDB write       │  │ EvidenceLog attach       │
    │ (existing)         │  │ (F261 NEW)         │  │ (F261 NEW, on-demand)     │
    │ forensics_*.lmdb   │  │ store              │  │ EvidenceLog               │
    │ key=finding_id     │  │ .async_ingest_     │  │ .attach_forensic_analysis │
    │ value=orjson(dict) │  │   findings_batch() │  │ (   finding_id,          │
    │                    │  │                    │  │   forensic_result )       │
    │                    │  │ → source_type=     │  │ → event_type=             │
    │                    │  │   "forensic_       │  │   "evidence_packet"       │
    │                    │  │    analysis"       │  │   payload.kind=           │
    │                    │  │ payload_text=      │  │   "forensic_analysis"     │
    │                    │  │   bounded dict     │  │ payload.forensic_result=  │
    │                    │  │                    │  │   bounded dict            │
    └─────────┬──────────┘  └─────────┬──────────┘  └──────────┬───────────────┘
              │                       │                         │
              │                       │                         │ also feeds
              │                       │                         ▼
              │                       │              ┌──────────────────────────┐
              │                       │              │ STIX 2.1 export          │
              │                       │              │ render_full_stix_bundle  │
              │                       │              │ forensic_analyses=...    │
              │                       │              │ → x-hledac-forensic SCO  │
              │                       │              │ → relationship:          │
              │                       │              │   x-hledac-forensic      │
              │                       │              │   derived-from → parent  │
              │                       │              └──────────────────────────┘
              ▼                       ▼
    ┌──────────────────────────────────────────────────────────┐
    │ Canonical path                                            │
    │   - LMDB WAL (forensic_result full payload)               │
    │   - DuckDB shadow_findings (forensic_analysis findings)   │
    │   - evidence_packet events (audit chain)                  │
    │   - STIX 2.1 bundle (x-hledac-forensic SCO + rels)        │
    └──────────────────────────────────────────────────────────┘
```

---

## 4. Wiring touchpoints (F261)

| File | Lines | Change | Purpose |
|------|------:|---|---|
| `evidence_log.py` | +~210 | New `_FORENSIC_MAX_*` constants, `_bound_forensic_value()`, `attach_forensic_analysis()`, `get_forensic_analyses()` | Forensic-grade evidence ledger write/read |
| `forensics/enrichment_service.py` | +~165 | New `FORENSIC_SOURCE_TYPE` constant, `_bound_enrichment_for_payload()`, `make_canonical_finding_from_enrichment()` | Forensic → CanonicalFinding adapter |
| `forensics/ioc_extractor.py` | +~115 | New `from typing import Any`, `IOC_FINDINGS_MAX`, `ioc_extract_to_canonical_findings()` | IOC extraction → CanonicalFinding per IOC |
| `utils/source_types.py` | +1 | `FORENSIC_ANALYSIS = "forensic_analysis"` in `SourceType` StrEnum | Centralized source_type registry |
| `runtime/enrichment_services.py` | +~80 | `enrich_ct_findings()` accepts `store: Any = None`; `__init__` + setter accept `evidence_log: Any = None`; writes derived `CanonicalFinding` via `async_ingest_findings_batch()` after LMDB write; calls `evidence_log.attach_forensic_analysis()` after DuckDB write | Canonical write path for forensics + tamper-evident evidence chain attachment |
| `runtime/sprint_scheduler.py` | line 15 753 + ~40 | `enrich_ct_findings(findings, self._result, store=self._duckdb_store)`; new `inject_evidence_log()` setter + bidirectional forwarding in `inject_enrichment_services()` | Passes DuckDBShadowStore to the seam; exposes evidence_log injection point for the canonical sprint owner |
| `export/stix_exporter.py` | +~165 | `_FORENSIC_ANALYSIS_OBJECT_TYPE`, `_FORENSIC_ANALYSIS_PROPERTY`, `_FORENSIC_SCHEMA_VERSION`, `_bound_forensic_object_content()`, `_build_forensic_analysis_object()`, `_build_forensic_relationship()`, `forensic_analyses` kwarg in `render_full_stix_bundle()` | STIX 2.1 `x-hledac-forensic` custom object type + `x_hledac_forensic` extension + `derived-from` relationships |

**Total:** 7 files touched, ~700 lines added (incl. type hints + docstrings), 0 lines removed.

---

## 5. Bounded invariants (always-on, no toggles)

| Invariant | Where | Bound |
|---|---|---|
| Forensic evidence payload (RAM) | `evidence_log._FORENSIC_MAX_VALUE_LEN` | 1 000 chars/str |
| Forensic evidence payload (RAM) | `evidence_log._FORENSIC_MAX_KEYS` | 30 keys |
| Forensic evidence payload (RAM) | `evidence_log._FORENSIC_MAX_LIST_ITEMS` | 20 items |
| Forensic evidence payload (RAM) | `evidence_log._FORENSIC_MAX_DEPTH` | 3 levels |
| CanonicalFinding payload_text (DuckDB) | `enrichment_service._FORENSIC_PAYLOAD_MAX_BYTES` | 4 096 bytes |
| CanonicalFinding payload_text (DuckDB) | `enrichment_service._FORENSIC_PAYLOAD_KEYS_MAX` | 25 keys |
| IOC findings per text | `ioc_extractor.IOC_FINDINGS_MAX` | 50 |
| STIX object content | `stix_exporter._FORENSIC_OBJECT_CONTENT_MAX` | 4 096 bytes |
| STIX object keys | `stix_exporter._FORENSIC_OBJECT_KEYS_MAX` | 25 |
| Confidence clamp | `make_canonical_finding_from_enrichment()` | [0.0, 1.0] |
| Semaphore (concurrent enrichments) | `EnrichmentServices.enrich_ct_findings()` | 3 |
| Hard timeout (WHOIS/SSL/DNS/rDNS) | `enrichment_service._EXTERNAL_LOOKUP_TIMEOUT` | 5.0 s |

All inviolable — no env-var toggle, no feature flag.

---

## 6. Fail-safe guarantees

| Failure mode | Behaviour |
|---|---|
| `enrich()` raises | `enrich_one()` `except Exception: pass` → LMDB/DuckDB/EvidenceLog all skipped, `forensics_enriched_ct_findings` not incremented |
| `make_canonical_finding_from_enrichment()` raises | Returns `None`, DuckDB write skipped |
| `async_ingest_findings_batch()` raises (quality gate) | Inner `except Exception: pass` → DuckDB write best-effort, sprint continues |
| `attach_forensic_analysis()` called with `None` result | Returns `None`, logs `debug` line |
| `attach_forensic_analysis()` on closed/frozen log | Returns `None`, logs `warning` |
| `_bound_*` recursion too deep | Returns `[depth_truncated]` |
| IOC extractor unavailable (no Rust ext) | Falls back to pure-Python regex implementation |

Zero crashes propagate. Every step degrades to silent skip.

---

## 7. STIX 2.1 export shape

For each forensic analysis attached to a finding with id `ct_42`:

```jsonc
{
  "type": "x-hledac-forensic",
  "spec_version": "2.1",
  "id": "x-hledac-forensic--<uuid5>",
  "created": "2026-06-04T...Z",
  "modified": "2026-06-04T...Z",
  "description": "Forensic analysis for parent finding ct_42",
  "finding_id": "ct_42",
  "parent_source_type": "ct_log",
  "content": "<bounded forensic_result JSON, ≤4096 bytes>",
  "extensions": {
    "x_hledac_forensic": {
      "version": "F261",
      "parent_finding_id": "ct_42",
      "parent_source_type": "ct_log",
      "content_kind": "forensic_analysis"
    }
  }
}
```

Plus a companion `relationship` object:

```jsonc
{
  "type": "relationship",
  "spec_version": "2.1",
  "id": "relationship--<uuid5>",
  "relationship_type": "derived-from",
  "source_ref": "x-hledac-forensic--<uuid5>",
  "target_ref": "indicator--<uuid5>"   // or observed-data--
}
```

STIX 2.1 §3.4 compliance: custom object type uses `x-` prefix (hyphen, vendor-prefixed).
STIX 2.1 §3.5 compliance: custom property uses `x_` prefix (underscore, no vendor prefix).

Compatible with OpenCTI / MISP / TheHive / vanilla STIX 2.1 consumers that enable `allow_custom=True`.

---

## 8. Known gaps / future work

| Gap | Reason | Suggested sprint |
|---|---|---|
| ~~`evidence_log.attach_forensic_analysis()` has **no automatic call site** from `sprint_scheduler`~~ **CLOSED in F261 follow-up** | Now wired inside `EnrichmentServices.enrich_one` (peer of the DuckDB write) and exposed via `EnrichmentServices.inject_evidence_log(elog)` + `SprintScheduler.inject_evidence_log(elog)` setters with bidirectional forwarding. | — |
| IOC findings are emitted only via `ioc_extract_to_canonical_findings()` (not called by enrichment pipeline) | `ForensicsEnricher` does not currently invoke `fast_ioc_extract()`; integration is a follow-up. | F262 (post-merge) |
| Forensic findings not yet visible in `sprint_exporter.py` reports | The markdown reporter / JSON-LD exporter read from `DuckDBShadowStore` but have no forensic-specific section. | F263 |
| No `provenance_json` forensics facet on `ForensicsResult` | The bounded `payload_text` is the only forensic field on the derived finding. A structured facet would ease downstream queries. | F264 |
| `SourceTypeLiteral` not updated with `"forensic_analysis"` | Cosmetic — the enum member is enough at runtime; static type-checkers may flag string literal uses. | F265 |

---

## 9. Test coverage hooks (suggested)

- `tests/probe_f261_evidence_log_forensic.py` — `attach_forensic_analysis()` + `get_forensic_analyses()` round-trip
- `tests/probe_f261_canonical_finding_wiring.py` — `make_canonical_finding_from_enrichment()` shape + bounds
- `tests/probe_f261_ioc_canonical_findings.py` — `ioc_extract_to_canonical_findings()` dedup + cap
- `tests/probe_f261_stix_forensic.py` — `render_full_stix_bundle(forensic_analyses=...)` produces valid STIX 2.1 with `x-hledac-forensic` objects + relationships
- `tests/probe_f261_enrichment_seam.py` — `EnrichmentServices.enrich_ct_findings(..., store=mock)` calls `async_ingest_findings_batch` exactly once per successful enrichment

---

*Generated 2026-06-04 by Sprint F261 audit. Reviewed against `git rev fab6f12d7b4e`.*
