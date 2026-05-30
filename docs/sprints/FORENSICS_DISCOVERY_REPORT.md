# FORENSICS_DISCOVERY_REPORT.md — Sprint F2FORENSICS_AUDIT

**Date:** 2026-05-24
**Scope:** forensics/, discovery/, deep_probe.py, intelligence/, FOCA_INTEGRATION_STATUS.md
**Boundary:** hledac/universal/

---

## Executive Summary

| Kategorie | Souborů | Wired ✓ | Isolated ✗ | Poznámka |
|-----------|---------|---------|------------|----------|
| forensics/ | 4 | 2 | 2 | metadata_extractor + enrichment_service aktivní |
| discovery/ | 11 | ~9 | ~2 | gopher_crawler + cascade možná isolated |
| deep_probe.py | 1 | ✓ | — | probe_runner.run_deep_probe_if_enabled() |
| intelligence/ | 47 | část | většina | ~20+ aktivních lane modulů |
| FOCA integrace | — | ✓ | 1 pending | x_originating_ip bridge |

**Isolated komponenty s hodnotou:**
1. `forensics/digital_ghost_detector.py` — skrytá data v byte streamech
2. `forensics/steganography_detector.py` — steganalýza obrázků
3. `discovery/cascade.py` + `gopher_crawler.py` — možná orphan (gopher_crawler má sidecar, cascade neznámý)
4. `intelligence/` — rozsáhlá síť intelligence lanes, většina neaktivní

---

## KROK 1 — Forensics Audit

### 1a. Modulový přehled

| Soubor | Řádků | Co dělá | Vstupy | Externí deps | Wired |
|--------|-------|---------|--------|---------------|-------|
| `metadata_extractor.py` | 2779 | Universal FOCA extrakce | file path | olevba (opt), hledac_rust_ext (opt) | ✓ |
| `enrichment_service.py` | 707 | ForensicsEnricher wrapper | CanonicalFinding | WHOIS, DNS, SSL | ✓ |
| `digital_ghost_detector.py` | 405 | Skrytá data: zlib ghosts, entropy, string fragments | bytes | ŽÁDNÉ | ✗ |
| `steganography_detector.py` | 222 | Chi-square + entropy steganalýza | image bytes | stegdetect (opt) | ✗ |

### 1b. ForensicsEnricher — enrich() flow (JIŽ ZAPOJEN)

```
enrich(findings: list[CanonicalFinding])
  └── enrich_batch(findings)
        └── enrich_one(finding) → tuple[finding_id, Optional[dict]]
              ├── _extract_file_path_from_payload()     ← extrahuje file path z payload
              ├── _file_has_forensics_support()         ← kontroluje příponu
              ├── UniversalMetadataExtractor.extract() ← FOCA extrakce
              ├── _score_foca_findings()               ← confidence scoring
              ├── _whois_lookup() / _ssl_lookup() / _dns_lookup() / _rdns_lookup()
              └── → dict[enrichment_data] → LMDB key=finding_id
```

**Výstup:** `forensics_enriched_ct_findings` counter v SprintSchedulerResult

### 1c. DigitalGhostDetector — detailní analýza

```python
analyze_file_ghosts(file_path: str) → DigitalGhostResult
analyze_directory_ghosts(directory_path: str) → list[DigitalGhostResult]

GhostArtifact:
  - artifact_type: str          # "zlib_ghost" | "byte_pattern" | "string_fragment" | "duplicate_pattern"
  - offset: int
  - size: int
  - entropy: float
  - description: str

DigitalGhostResult:
  - file_path: str
  - success: bool
  - artifacts: list[GhostArtifact]
  - overall_suspicious: float    # 0.0-1.0
  - zlib_occurrences: int
  - scan_errors: list[str]
```

**Detekční metody:**
- `_detect_zlib_ghosts(data: bytes)` — hledá zlib-komprimované bloky v nekomprimovaných datech
- `_detect_byte_pattern_anomalies(data: bytes)` — entropy shluky, opakující se vzory
- `_calculate_entropy(data: bytes)` — entropy histogram
- `_detect_string_fragments(data: bytes)` — ASCII string fragmenty v binárních datech
- `_detect_duplicate_patterns(data: bytes)` — duplicitní vzory v datech

**Proč je hodnotný:** Detekuje skrytá data v souborech (steganografie, embednutý malware, skryté archivy) — **data jinak nedostupná** kanonickou pipeline.

### 1d. SteganalysisDetector — detailní analýza

```python
analyze_image_steganography(file_path: str) → SteganalysisResult
chi_square(data: bytes) → float        # chi-square test na LSB
entropy(data: bytes) → float          # entropy analýza

SteganalysisResult:
  - file_path: str
  - stegdetect_available: bool
  - chi_square_score: float            # vysoký = podezřelý
  - entropy_score: float               # vysoký = podezřelý
  - overall_suspicious: float          # 0.0-1.0
  - method: str                        # "chi_square" | "entropy" | "stegdetect"
```

**Proč je hodnotný:** Detekuje steganografii v obrazech bez externích nástrojů (čistý Python fallback), low-memory footprint.

---

## KROK 2 — Discovery Audit

### 2a. Discovery Adapter Landscape

| Soubor | Řádků | Provider | Typ | Wired | Poznámka |
|--------|-------|----------|-----|-------|----------|
| `crtsh_adapter.py` | 1259 | crt.sh CT | subdomain | ✓ | Cooldown, cache |
| `circl_pdns_adapter.py` | 726 | CIRCL PDNS | passive DNS | ✓ | Registered in source_registry |
| `duckduckgo_adapter.py` | 1478 | DDG + Mojeek | web search | ✓ | Stealth session |
| `discovery_planner.py` | 672 | orchestrátor | multi-provider | ✓ | Central seam |
| `wayback_cdx_adapter.py` | 279 | Wayback CDX | historical | ✓ | |
| `rss_atom_adapter.py` | 2075 | RSS/ATOM | feed agg | ⚠️ | Wire unknown |
| `ti_feed_adapter.py` | 1967 | Threat Intel | TI feeds | ⚠️ | Wire unknown |
| `gopher_crawler.py` | 332 | Gopher | protocol | ✓ | _run_gopher_sidecar |
| `source_registry.py` | 188 | registry | src adapter | ✓ | CIRCL PDNS registered |
| `fusion_ranker.py` | 340 | ranker | fusion/scoring | ⚠️ | Wire unknown |
| `cascade.py` | 320 | cascade | multi-source | ⚠️ | Nepoužitý? |
| `historical_frontier.py` | 196 | historical | frontier | ⚠️ | Wire unknown |

### 2b. source_registry — Registered Providers

```python
_SOURCE_REGISTRY: dict[str, SourceEntry]

Registered (zjištěno z kódu):
- circl_pdns: tier=1, acquisition_lane="passive_dns"
- crtsh: (CT subdomain enumeration)
- (další registrace možná v __init__ nebo lazy)

SourceEntry fields:
  adapter: Callable
  tier: int              # 1=nejvyšší (free, no API key)
  acquisition_lane: str  # "passive_dns", "ct", atd.
```

### 2c. Gopher Sidecar — ZJIŠTĚNÍ

`_run_gopher_sidecar()` existuje v sprint_scheduler — **gopher_crawler JE zapojen** jako sidecar.

### 2d. Cascade.py — neznámý modul

```python
# Třída: CASCADE (320L)
# Bez docstringu, bez jasného účelu
# Možná orphaned — ověřit exists + wiring
```

**Doporučení:** cascade.py projít detailně — možná orphan, možná důležitý.

---

## KROK 3 — FOCA Integration Status (aktualizovaný)

### FOCA_INTEGRATION_STATUS.md — Sprint FOCADI-16

**Phase 1-4 COMPLETE:**

| Feature | Status | Location |
|---------|--------|----------|
| PPTX/ODP metadata extraction | ✓ Done | metadata_extractor.py:2088 |
| Email header forensics | ✓ Done | metadata_extractor.py:2253 |
| CAD/SVG/DXF metadata | ✓ Done | |
| TriageFacets.metadata wiring | ✓ Done | evidence_triage.py:422-459 |
| Macro URL extraction (olevba) | ✓ Done | metadata_extractor.py:63-100 |
| OfficeDocumentAnalyzer FOCA seam | ✓ Done | intelligence/document_intelligence.py |
| Confidence scoring _score_foca_findings | ✓ Done | enrichment_service.py:476 |
| **Entity extraction bridge** | ✗ PENDING | EmailMetadata.x_originating_ip → NetworkIntelligence |

**PENDING Bridge detail:**
```
EmailMetadata.x_originating_ip
  → NetworkIntelligence.lookup()   ← neimplementováno
  → přidává WHOIS/rdDNS context k email findings
```

---

## KROK 4 — Intelligence Directory (47 souborů)

### 4a. Intel moduly — plný přehled

| Soubor | Řádků | Co dělá | Wired |
|--------|-------|---------|-------|
| `pattern_mining.py` | 2032 | Behavioral pattern detection | ? |
| `passive_fingerprint.py` | 1667 | Passive service fingerprinting | ✓ (F204G) |
| `exposure_correlator.py` | 1129 | Asset exposure correlation | ✓ (F202C) |
| `document_intelligence.py` | 2235 | Doc metadata + FOCA seam | ✓ |
| `identity_stitching.py` | 1295 | Cross-platform identity linking | ✓ (F202B) |
| `kill_chain_tagger.py` | 946 | MITRE ATT&CK mapping | ? |
| `network_reconnaissance.py` | 1388 | WHOIS + DNS enumeration | ⚠️ isolated |
| `temporal_archaeologist.py` | 1477 | Temporal content recovery | ✓ (F202E) |
| `dark_web_intelligence.py` | 916 | Tor/I2P crawling | ✓ (F202H?) |
| `data_leak_hunter.py` | 1012 | Breach + leak monitoring | ✓ (F202D) |
| `stealth_crawler.py` | 3082 | Stealth web crawling | ✓ |
| `attribution_scorer.py` | 662 | Confidence scoring for identity | ? |
| `entity_signal_extractor.py` | 330 | Entity extraction from findings | ✓ (F202B) |
| `relationship_discovery.py` | 2443 | Social network analysis | ? |
| `academic_search.py` | 1402 | Academic search system | ? |
| `workflow_orchestrator.py` | 1848 | Multi-module coordination | ? |
| `open_source_collectors.py` | 1146 | Public OSINT sources | ? |
| `web_intelligence.py` | 1449 | Lightweight scraping | ? |
| `streaming_embedder.py` | 293 | Chunked async embeddings | ? |
| `temporal_analysis.py` | 804 | Historical trend analysis | ? |
| `social_identity_miner.py` | 660 | Social identity surface mining | ? |
| `exposed_service_hunter.py` | 1682 | Exposed services discovery | ? |
| `exposure_clients.py` | 1082 | Mixed exposure clients | ? |
| `github_secret_scanner.py` | 432 | GitHub code search | ✓ (F202D) |
| `pastebin_monitor.py` | 386 | Paste site monitoring | ✓ (F202D) |
| `censys_lane.py` | 188 | Censys intelligence lane | ? |
| `greynoise_lane.py` | 238 | GreyNoise lane | ? |
| `shodan_lane.py` | 187 | Shodan lane | ? |
| `shodan_wrapper.py` | 219 | Shodan API wrapper | ? |
| `ct_lane.py` | 264 | CT intelligence lane | ? |
| `bgp_lane.py` | 589 | BGP/ASN IP-to-Org | ? |
| `network_intelligence.py` | 364 | BGP + DoH | ? |
| `doh_lane.py` | 347 | DNS-over-HTTPS lane | ? |
| `blockchain_analyzer.py` | 1595 | Blockchain forensics | ? |
| `cryptographic_intelligence.py` | 1256 | Cryptanalysis | ? |
| `input_detector.py` | 939 | Input type detection | ? |
| `onion_seed_manager.py` | 210 | .onion seed management | ? |
| `confidence_policy.py` | 184 | Canonical confidence policy | ? |
| `archive_discovery.py` | 1874 | Wayback + Archive.today | ? |
| `advanced_image_osint.py` | 635 | Image OSINT | ? |
| `wayback_cdx.py` | 451 | CDX deep search | ? |
| `wayback_diff_miner.py` | 610 | Wayback diff mining | ? |
| `ct_log_client.py` | 312 | CT log client | ? |
| `rir_correlator.py` | 647 | RIR/ASN/WHOIS correlator | ? |
| `identity_stitching_canonical.py` | 507 | Canonical adapter for identity | ✓ (F202B) |
| `temporal_archaeologist_adapter.py` | 285 | Adapter for temporal archaeologist | ✓ (F202E) |
| `leak_sentinel.py` | 611 | Leak + secret sentinel | ✓ (F202D) |

### 4b. Capability enum (34 hodnot)

```
1. GRAPH_RAG       13. ENTITY_LINKING    25. STEGO
2. RERANKING       14. DOC_INTEL        26. BLOCKCHAIN
3. STEALTH         15. IMAGE            27. SNN
4. DARK_WEB        16. AUDIO            28. FEDERATED
5. TOR             17. VIDEO           29. QUANTUM_PATH
6. I2P             18. OCR              30. QUANTUM_PQ
7. PREFETCH        19. METADATA_EXTRACT 31. META_OPTIMIZER
8. FORECAST        20. TEMPORAL         32. TOT
9. TEMPORAL        21. PATTERN_MINING   33. HERMES
10. INSIGHT        22. INSIGHT          34. MODERNBERT
11. CRYPTO_INTEL   23. CRYPTO_INTEL    35. GLINER
12. PATTERN_MINING 24. STEGO
```

**CHYBÍCÍ Capability pro forensics:**
- `DIGITAL_GHOST_DETECTION` — pro digital_ghost_detector.py
- `FORENSICS_METADATA` — už existuje jako `METADATA_EXTRACT` ✓

---

## KROK 5 — Deep Probe Integrace (AKTIVNÍ)

### 5a. Aktuální stav — WIRED ✓

```
deep_probe.py
  └── DeepProbeScanner class (line 556)
        ├── scan(domain: str) → list[str]
        ├── scan_deep_web(target_url, options)
        ├── scan_s3_buckets(domain) → Tuple[List[dict], List[CanonicalFinding]]
        ├── scan_ipfs(keyword) → List[Dict]
        ├── wayback_discovery(url)
        └── predict_hidden_paths(base_url, known_paths)

probe_runner.py (deep_research/)
  └── run_deep_probe_if_enabled(query, store, deep_probe_enabled=False)
        ├── run_deep_probe() → dict
        │     ├── _run_discovery()    → ShadowWalker + Dorking
        │     ├── _run_bucket_scan()  → S3/GCS/Azure blob scan
        │     └── _run_ipfs()         → IPFS scan
        └── canonical write: async_ingest_findings_batch()
```

**Canonical entry:** `core/__main__.py` volá `run_deep_probe_if_enabled()` po sprint exportu — **JE ZAPOJEN**.

### 5b. Co deep_probe.skenuje navíc oproti discovery/ 

| Co | deep_probe | discovery/ |
|----|-----------|------------|
| S3/GCS/Azure blob discovery | ✓ | ✗ |
| IPFS content scan | ✓ | ✗ |
| Deep web path prediction | ✓ (ShadowWalker) | ✗ |
| Wayback historical URL | ✓ | ✓ (wayback_cdx_adapter) |
| CT subdomain enumeration | ✗ | ✓ (crtsh_adapter) |
| PDNS lookup | ✗ | ✓ (circl_pdns_adapter) |
| Web search | ✗ | ✓ (duckduckgo_adapter) |

**Conclusion:** deep_probe ≠ discovery — doplňkový, specializovaný na skrytý obsah (S3, IPFS, deep web).

---

## KROK 6 — Priority Matrix

### Isolated forensic components s hodnotou

| # | Komponenta | Hodnota | Effort | Priorita |
|---|------------|---------|--------|----------|
| 1 | **digital_ghost_detector.py** | Skrytá data v byte streamech | Nízký | **HIGH** |
| 2 | **steganography_detector.py** | Steganografie v obrazech | Nízký | **HIGH** |

### Isolated discovery components s hodnotou

| # | Komponenta | Hodnota | Effort | Poznámka |
|---|------------|---------|--------|----------|
| 3 | **cascade.py** | ? — neznámý účel | Střední | Nutno analyzovat |
| 4 | **ti_feed_adapter.py** | Threat intelligence feeds | Střední | Ověřit wiring |
| 5 | **rss_atom_adapter.py** | RSS/ATOM aggregation | Střední | Ověřit wiring |
| 6 | **fusion_ranker.py** | Cross-source ranking | Nízký | Možná již integrováno |

### Isolated intelligence components s hodnotou

| # | Komponenta | Hodnota | Poznámka |
|---|------------|---------|----------|
| 7 | **network_reconnaissance.py** | WHOIS/DNS enumeration | Duplicita s ForensicsEnricher WHOIS |
| 8 | **pattern_mining.py** (2032L) | Behavioral pattern detection | Velký, možná důležitý |
| 9 | **kill_chain_tagger.py** (946L) | MITRE ATT&CK mapping | Koreluje s threat intel |
| 10 | **relationship_discovery.py** (2443L) | Social network analysis | Velký, možná důležitý |

---

## KROK 7 — Doporučené Akce

### IMMEDIATE (1 sprint)

#### A. DigitalGhostDetector activation

```python
# capabilities.py — add
DIGITAL_GHOST_DETECTION = "digital_ghost_detection"

# sprint_scheduler.py — add sidecar
async def _run_digital_ghost_sidecar(self, targets: list[str]) → list[CanonicalFinding]:
    """Sidecar for digital ghost detection on file payloads."""
    # ENV gate: HLEDAC_ENABLE_DIGITAL_GHOST=1
    # Bound: MAX_FILES=10 per sprint
    # Input: file payloads from findings
    # Output: CanonicalFinding(source_type="digital_ghost", ghost artifacts)
    # Pattern: fail-soft, gather(return_exceptions=True)
```

#### B. SteganographyDetector activation

```python
# capabilities.py — already has STEGO = "stego" ✓

# sprint_scheduler.py — add sidecar
async def _run_steganography_sidecar(self, targets: list[str]) → list[CanonicalFinding]:
    """Sidecar for steganalysis on image payloads."""
    # ENV gate: HLEDAC_ENABLE_STEGANOGRAPHY=1
    # Bound: MAX_IMAGES=10, max_file_size_mb=50
    # Input: image payloads from findings
    # Output: CanonicalFinding(source_type="steganography", chi_square + entropy scores)
    # Pattern: fail-soft, _check_stegdetect() pro binary availability
```

### SHORT-TERM (2-3 sprinty)

#### C. Cascade.py forensic audit
- Ověřit co přesně dělá
- Rozhodnout: zapojit nebo odstranit orphaned kód

#### D. NetworkReconnaissance vs ForensicsEnricher deduplication
- ForensicsEnricher má `_whois_lookup`, `_ssl_lookup`, `_dns_lookup`
- network_reconnaissance.py má plnou WHOIS/DNS enumeration (1388L)
- Tyto dvě vrstvy se pravděpodobně překrývají — sjednotit

#### E. Intelligence lane audit
- 47 souborů v intelligence/, většina necharacterizovaná
- Provést hloubkový audit: které lanes jsou aktivní, které jsou orphaned
- Možná konsolidace: intelligence/*.py → canonical lanes vs. orphaned

#### F. FOCA x_originating_ip bridge
- `EmailMetadata.x_originating_ip` → NetworkIntelligence lookup
- Jeden malý task, ale vyžaduje pochopení enrichment queue systému

### MEDIUM-TERM

#### G. Pattern_mining.py activation (2032L)
- Behavioral pattern detection — možná velmi hodnotný pro correlaci
- Wire do pattern_mining capability lane

#### H. Kill_chain_tagger.py activation (946L)
- MITRE ATT&CK mapping — standard v OSINT
- Wire do threat intel pipeline

---

## Architecture Map

```
hledac/universal/
├── forensics/
│   ├── metadata_extractor.py   ─── FOCA ──→ TriageFacets.metadata + OfficeDocumentAnalyzer
│   ├── enrichment_service.py   ──── ForensicsEnricher ──→ sprint_scheduler._enrich_ct_findings_forensics
│   ├── digital_ghost_detector.py ✗ ISOLATED ← aktivovat
│   └── steganography_detector.py ✗ ISOLATED ← aktivovat
├── discovery/
│   ├── discovery_planner.py     ─── orchestrátor ──→ sprint_scheduler sidecary
│   ├── crtsh_adapter.py        ✓
│   ├── circl_pdns_adapter.py    ✓ (source_registry registered)
│   ├── duckduckgo_adapter.py   ✓
│   ├── wayback_cdx_adapter.py  ✓
│   ├── gopher_crawler.py       ✓ (_run_gopher_sidecar)
│   ├── source_registry.py     ✓ (registry pattern)
│   ├── fusion_ranker.py        ⚠️ ?wire
│   ├── rss_atom_adapter.py     ⚠️ ?wire
│   ├── ti_feed_adapter.py      ⚠️ ?wire
│   ├── cascade.py              ⚠️ UNKNOWN
│   └── historical_frontier.py  ⚠️ ?wire
├── deep_research/
│   └── probe_runner.py         ✓ run_deep_probe_if_enabled() — canonical
├── intelligence/               ⚠️ 47 modules, většina ?wire
│   ├── network_reconnaissance.py ⚠️ isolated (duplicates ForensicsEnricher WHOIS)
│   ├── pattern_mining.py       2032L — velký, ?wire
│   ├── kill_chain_tagger.py    946L — MITRE ATT&CK
│   ├── relationship_discovery.py 2443L — velký
│   └── [43 dalších]           ?wire/?orphaned
└── capabilities.py            34 enum values, DIGITAL_GHOST_DETECTION chybí
```

---

## Závěr

**Forensics:** 2/4 modulů zapojeno, 2 izolované s vysokou hodnotou — **AKTIVOVAT DigitalGhostDetector + Steganalysis**.

**Discovery:** Většina zapojená přes discovery_planner + sidecary. Gopher_crawler má sidecar. Cascade.py je neznámý — nutno forenzovat.

**Deep probe:** JE ZAPOJEN přes probe_runner.run_deep_probe_if_enabled() — canonical entry point v core/__main__.py.

**Intelligence:** 47 modulů, rozsáhlá síť lanes. Většina necharacterizovaná. Doporučení: samostatný sprint na intel lane audit.

**FOCA:** Phase 1-4 complete, 1 pending bridge (x_originating_ip).

**CHYBÍ:**
- `Capability.DIGITAL_GHOST_DETECTION` — není v enum
- `_run_digital_ghost_sidecar()` — není v sprint_scheduler
- `_run_steganography_sidecar()` — není v sprint_scheduler
- `_run_cascade_sidecar()` — možná nutná, záleží na cascade.py účelu