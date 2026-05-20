# OSINT Capability Coverage Audit — 2026-05-20 (F256A)

**Sprint:** F256A
**Date:** 2026-05-20
**Goal:** Full capability activation matrix — canonical live vs offline/replay vs implemented-not-wired vs dormant vs M1-risky. No code changes.

## Scope (F256A expanded)
- `discovery/` — public/Darkweb discovery adapters
- `intelligence/` — TI feeds, leak sentinels, academic search
- `pipeline/` — live sprint pipelines
- `transport/` — Tor/I2P/Nym/stealth transports
- `tools/` — capability utilities
- `runtime/acquisition_strategy.py` — lane definitions
- `runtime/sidecar_bus.py` — sidecar runner registry
- `runtime/sprint_scheduler.py` — canonical sprint execution
- `brain/` — ML/AI model engines
- `knowledge/` — DuckDB, LanceDB, graph storage
- `export/` — report builders
- `docs/audits/SIDECAR_ACTIVATION_REALITY_REFRESH.md` — sidecar classification
- `docs/audits/DISCOVERY_OFFLINE_REPLAY_AUDIT.md` — replay infrastructure
- `docs/audits/OFFLINE_PROVIDER_YIELD_DIAGNOSIS.md` — offline yield analysis
- `pyproject.toml` extras

**Activation status codes:**
- `LIVE_ACTIVE` — canonical live sprint, fully wired
- `LIVE_ACTIVE_BUT_NOT_VISIBLE` — runs, no CanonicalFinding output or no report visibility
- `OFFLINE_ONLY` — read-only path, no live network
- `IMPLEMENTED_NOT_WIRED` — file/module exists but no pipeline wiring
- `DORMANT` — exists, not referenced anywhere
- `DISABLED_BY_POLICY` — feature-flagged off or blocked by M1 governor policy
- `GAP` — referenced in design but not implemented at all
- `M1_RISK_HEAVY` — RAM/memory heavy, under governor guard

---

## 1. Capability Matrix (as-implemented)

| # | Capability | Owner Module | Dependency Extra | Enabled Default | Fail-Soft | Has Tests | M1 8GB Cost | Maturity |
|---|-----------|--------------|-----------------|----------------|-----------|-----------|-------------|----------|
| 1 | **Public web search** | `discovery/duckduckgo_adapter.py` | `search` (duckduckgo-search>=8.0.0) | ✅ m1-local / default | ✅ RatelimitException/TimeoutException | ✅ F206AB | ~50MB | **production** |
| 2 | **CT logs** (crt.sh) | `discovery/crtsh_adapter.py` | default (aiohttp) | ✅ always | ✅ HTTP 5xx→cooldown | ✅ F217D/F219E/F224/F234D | ~20MB | **production** |
| 3 | **Wayback/CDX** (archive.org) | `discovery/wayback_cdx_adapter.py` | default | ✅ always | ✅ HTTP 5xx→empty | ✅ F206AM | ~20MB | **production** |
| 4 | **TI feeds** (ThreatFox, URLhaus, CISA KEV, Feodo, OpenPhish) | `discovery/ti_feed_adapter.py` | default | ✅ DEFAULT_SOURCE_TYPES | ✅ all errors | ✅ probe tests | ~20MB | **production** |
| 5 | **Academic search** (ArXiv, Crossref, Semantic Scholar) | `intelligence/academic_search.py` | default | ❌ opt-in via ACADEMIC profile | ✅ HTTP errors | ✅ F207F/R9 | ~40MB | **production** (R9) |
| 6 | **Pastebin monitor** | `intelligence/pastebin_monitor.py` | default | ✅ tier 1 source | ✅ all errors | ✅ F202D | ~20MB | **production** (F202D) |
| 7 | **GitHub secret scanner** | `intelligence/github_secret_scanner.py` | default | ✅ tier 1 source | ✅ all errors | ✅ F202D | ~30MB | **production** (F202D) |
| 8 | **IPFS scanner** | `network/ipfs_client.py` + `deep_probe.py` | default | ✅ via deep_probe sidecar | ✅ scan failures | ✅ test_ipfs_canonical, test_deep_probe_runner | ~30MB | **advisory** (F197A) |
| 9 | **Graph correlation** (DuckPGQ) | `knowledge/graph_service.py` | default (duckdb in deps) | ✅ always | ✅ fail-safe all methods | ✅ F195C | ~50MB | **production** (F195C) |
| 10 | **LanceDB ANN dedup** | `knowledge/lancedb_store.py` | graph-storage | ✅ m1-local / graph-storage | ✅ fail-open | ✅ F200B | ~100MB | **production** (F200B) |
| 11 | **MLX local inference** | `brain/hermes3_engine.py` | apple-accel | ✅ m1-local only | ⚠️ OOM guard via uma_budget | ✅ F183C+ | ~2GB (model) | **production** |
| 12 | **OCR (pytesseract)** | `multimodal/analyzer.py` | ocr (pytesseract) | ❌ opt-in | ✅ ImportError→skip | ✅ F202I | ~50MB | **production** (F202I) |
| 13 | **Browser rendering** (camoufox/nodriver) | `multimodal/analyzer.py` + `tools/lightpanda_pool.py` | browser (camoufox,nodriver) | ❌ opt-in | ✅ lazy import | ✅ F202I | ~2GB+ | ⚠️ **advisory** — OOM on M1+MLX |
| 14 | **STIX/JSON/Markdown export** | `export/sprint_exporter.py` | default | ✅ always | ✅ fail-soft | ✅ F214 | ~10MB | **production** |
| 15 | **Tor transport** | `transport/tor_transport.py` | tor + transport extras | ❌ opt-in (stem lazy) | ✅ TorUnavailableError | ✅ F202H | ~50MB | **production** (F202H) |
| 16 | **Stealth JA3 fingerprint** | `fetching/public_fetcher.py` | osint-html (curl_cffi) | ✅ m1-local | ✅ always-on | ✅ F202H | ~20MB | **production** |
| 17 | **DuckDuckGo HTML fallback** | `fetching/public_fetcher.py` | default | ✅ always | ✅ fail-soft | ✅ F206AB | ~20MB | **production** |

---

## 2. Capabilities Referenced but NOT Implemented

| Capability | Where Referenced | Status |
|-----------|-----------------|--------|
| **CIRCL PDNS passive DNS** | `nonfeed_candidate_ledger.py`, `source_finding_bridge.py` ("circl_pdns" in source tier, `passive_dns_results_to_findings`) | ❌ **GAP** — only stub in source_tier, no `call_circl_pdns()` implementation found |
| **Common Crawl** | `tools/commoncrawl_adapter.py` exists | ⚠️ **dormant** — file exists but NOT wired into any pipeline or source tier |
| **RDAP WHOIS** | `runtime/acquisition_strategy.py` mentions RDAP lanes | ⚠️ **GAP** — no `call_rdap()` / `async_whois()` implementation found in discovery/ or tools/ |
| **I2P transport** | `transport/i2p_transport.py` (280+ lines) | ⚠️ **dormant** — exists but NOT wired into discovery pipeline or source tier |
| **Nym transport** | `transport/nym_transport.py` (500+ lines) | ⚠️ **dormant** — exists but NOT wired into discovery pipeline or source tier |
| **Onion discovery** | `pipeline/live_public_pipeline.py` imports "onion_discovery" | ⚠️ **dormant** — referenced in source_tier and sprint_exporter, but no `onion_discovery.py` found in discovery/ or intelligence/ |

---

## 3. Dormant Capabilities (exist but not wired)

| Capability | File | Lines | Why Dormant |
|-----------|------|-------|-------------|
| Common Crawl | `tools/commoncrawl_adapter.py` | ~200 | Not in source_tier, not called by any pipeline |
| stealth_crawler | `intelligence/stealth_crawler.py` | 3082 | Large module — referenced but not in sprint source_tier; likely pre-F202H dead code |
| Darknet utilities | `tools/darknet.py` | ~200 | Named but no call sites found |
| HNSW ANN builder | `tools/hnsw_builder.py` | ~300 | Not called by lancedb_store (uses pyarrow instead) |
| IPFS client | `network/ipfs_client.py` | ~200 | Called by deep_probe only; scan_ipfs() not in main pipeline |

---

## 4. Duplicate Capabilities

| Duplicate | Evidence |
|-----------|----------|
| **DuckDB is canonical write + analytics** | `duckdb_store.py` used for both canonical writes AND DuckPGQ graph analytics; `graph_service.py` wraps DuckPGQGraph; no separate analytics DB needed |
| **CT provider resilience** (5 parallel adapters vs unified) | F217D added cooldown states to crtsh_adapter; F234D added parallel CT providers; but no unified CT provider registry — each adapter is standalone |
| **Nonfeed candidate ledger** vs **acquisition strategy** | `nonfeed_candidate_ledger.py` (F214) and `runtime/acquisition_strategy.py` (F206K) both do domain candidate extraction/ranking; some overlap in `source_finding_bridge.py` |

---

## 5. Capability Gaps (NOT implemented at all)

| Gap | Impact | Priority |
|----|--------|---------|
| **CIRCL PDNS passive DNS** | Cannot resolve current/recent DNS records for domains; critical for pivot from domain→IP | HIGH |
| **RDAP WHOIS** | No WHOIS/RDAP for domain/IP registration data; critical for pivot from domain→registrant | HIGH |
| **Common Crawl wet API** | Large-scale web archive access beyond Wayback CDX; useful for historical snapshots | MEDIUM |
| **Onion site discovery** (not Tor transport, but .onion crawler) | Cannot discover new .onion sites; only follows known onion links | MEDIUM |
| **I2P eepProxy gateway** | Cannot reach I2P destinations (hosts behind I2P) | LOW |
| **Nym mixnet** | Cannot use Nym mixnet for anonymous requests | LOW |

---

## 6. extras Dependency Map (from pyproject.toml)

```
default        → transformers, duckdb, lancedb, aiohttp, httpx (canonical write + ANN + HTTP)
m1-local       → apple-accel + osint-html + graph-storage + acceleration + transport
apple-accel    → mlx, mlx-lm, uvloop
osint-html     → selectolax, curl_cffi, h2, xxhash
graph-storage  → duckdb, lancedb, pyarrow, polars
acceleration   → uvloop
transport      → stem, h2, aiohttp-socks
search         → duckduckgo-search>=8.0.0
torch          → torch, torchvision (NEVER default — separate install)
light          → fast-langdetect, datasketch
ocr            → pytesseract
browser        → camoufox[geoip], nodriver
rerank         → flashrank
```

**Note:** `kuzu` (graph-truth extra) has no cp314 arm64 wheel — install from source required. Not in m1-local.

---

## 7. Best Next OSINT Improvement for M1-Local

### Recommendation: Implement CIRCL PDNS passive DNS (Priority 1)

**Why:**
1. Referenced everywhere in F214/F223 nonfeed lanes but **not actually implemented**
2. `nonfeed_candidate_ledger.py` has `compute_lane_eligibility` for passive_dns but no `call_circl_pdns()` function
3. `source_finding_bridge.py` has `passive_dns_results_to_findings` converter but no actual PDNS API caller
4. High impact: enables domain→IP pivot with historical DNS records
5. Low cost: CIRCL PDNS is free, no API key required (rate-limited)

**Estimated M1 cost:** ~20MB RAM, single HTTP call per query
**Implementation path:**
- Add `circl_pdns_adapter.py` in `discovery/` (mirrors `crtsh_adapter.py` pattern)
- Wire `circl_pdns` into `nonfeed_candidate_ledger.py` lane planner
- Add `call_circl_pdns()` → `async_search_circl_pdns(domain, timeout_s=5.0)`
- CIRCL PDNS endpoint: `https://pdns.circl.lu/lookup/{type}/{query}`

### Secondary: RDAP WHOIS (Priority 2)

**Why:** Domain→registrant pivot missing; RDAP is standardized and widely available:
- `https://rdap.verisign.com/` (com/net)
- `https://rdap.org/` (multi-TLD)
- No API key for basic queries

---

## 8. Acquisition Lane Matrix

### 8a. Feed Lanes (live active)

| Lane | Owner | Canonical Live | Offline/Replay | Entry Point | M1 Risk |
|------|-------|--------------|----------------|-------------|---------|
| **feed** (TI feeds) | `discovery/ti_feed_adapter.py` | ✅ LIVE_ACTIVE | ❌ no offline fixtures | `_run_feed_branch()` → `run_feed_lane()` | LOW |
| **CT logs** (crt.sh) | `discovery/crtsh_adapter.py` | ✅ LIVE_ACTIVE | ⚠️ design proposed, no fixtures | `_run_ct_branch()` → `_run_ct_log_discovery_in_cycle()` | LOW |

### 8b. Nonfeed Lanes (live active)

| Lane | Owner | Canonical Live | Offline/Replay | Entry Point | M1 Risk |
|------|-------|--------------|----------------|-------------|---------|
| **public** (DuckDuckGo) | `discovery/duckduckgo_adapter.py` | ✅ LIVE_ACTIVE | ⚠️ design proposed, no fixtures | `_run_public_branch()` → `_run_public_discovery_in_cycle()` | MEDIUM (~50MB) |
| **wayback** | `discovery/wayback_cdx_adapter.py` | ✅ LIVE_ACTIVE | ⚠️ design proposed, no fixtures | `_run_wayback_prelude_lane()` | LOW |
| **passive_dns** | `network/passive_dns.py` | ✅ LIVE_ACTIVE via advisory | ❌ no replay | `_run_pdns_prelude_lane()` → `PassiveDNSAdapter` | LOW |
| **doh** (DNS-over-HTTPS) | `intelligence/doh_lane.py` | ✅ LIVE_ACTIVE | ❌ no replay | `_run_doh_prelude_lane()` | MEDIUM |
| **nonfeed** (domain candidates) | `runtime/acquisition_strategy.py` | ✅ LIVE_ACTIVE | ⚠️ dry-run only | `_run_nonfeed_prelude_gather()` | MEDIUM |

### 8c. Advisory Lanes (canonical live, advisory-only)

| Lane | Owner | Canonical Live | Offline/Replay | Entry Point | M1 Risk |
|------|-------|--------------|----------------|-------------|---------|
| **CT→PDNS pivot** | `sprint_scheduler._run_ct_to_passivedns_pivot_advisory()` | ✅ LIVE_ACTIVE | ❌ no replay | `_run_advisory_runner()` → `run_ct_to_passivedns_pivot_advisory()` | LOW |
| **BGP advisory** | `sprint_scheduler._run_bgp_advisory_sidecar()` | ✅ LIVE_ACTIVE | ❌ no replay | `_run_advisory_runner()` → `run_bgp_advisory_sidecar()` | MEDIUM |
| **wayback_cdx_deep** | `sprint_scheduler._run_wayback_cdx_deep_sidecar()` | ✅ LIVE_ACTIVE | ❌ no replay | `_run_advisory_runner()` → `run_wayback_cdx_deep_sidecar()` | MEDIUM |
| **pivot_planner** | `runtime/acquisition_strategy.py` | ✅ LIVE_ACTIVE | ❌ no replay | `_run_advisory_runner()` → `run_pivot_planner_advisory()` | LOW |
| **pivot_executor** | `runtime/sidecar_orchestrator.py` | ✅ LIVE_ACTIVE | ❌ no replay | `_run_advisory_runner()` → `run_pivot_executor_advisory()` | LOW |

### 8d. Not Wired / GAP

| Lane | Owner | Status | Gap Type |
|------|-------|--------|---------|
| **CIRCL PDNS** | `discovery/circl_pdns_adapter.py` (stub) | ❌ IMPLEMENTED_NOT_WIRED | `call_circl_pdns()` exists in plans, not in code |
| **RDAP WHOIS** | none | ❌ GAP | No `call_rdap()` / `async_whois()` anywhere |
| **Onion discovery** | none | ❌ GAP | `source_tier` references "onion_discovery", file doesn't exist |
| **Common Crawl** | `tools/commoncrawl_adapter.py` | ⚠️ DORMANT | File exists, not in source_tier, no pipeline call |

---

## 9. Sidecar Activation Matrix

**Source:** `runtime/sidecar_bus.py` — `DEFAULT_SIDECAR_RUNNERS` (16 runners), `SIDECAR_STAGES`, `_HEAVY_SIDECARS`

### 9a. Sidecar Classification Summary

| Sidecar | Canonical Live | Active Network | RAM Heavy | Probe Tests | Notes |
|---------|--------------|----------------|-----------|-------------|-------|
| `leak_sentinel` | ✅ LIVE_ACTIVE | ✅ live HTTP (pastebin, github, data_leak) | ❌ | ✅ F202D | source_type=redacted |
| `network_intel` | ✅ LIVE_ACTIVE | ✅ HTTP/TCP (passive DNS, BGP, fingerprint) | ❌ | ✅ F214 | source_type=network_intel |
| `banner_grab` | ✅ LIVE_ACTIVE | ✅ socket.connect() TCP | ✅ HEAVY | ✅ F204H | tag only |
| `ipv6_recon` | ✅ LIVE_ACTIVE | ✅ DNS resolution | ✅ HEAVY | ✅ F204H | tag only |
| `rir_correlator` | ✅ LIVE_ACTIVE | ✅ HTTP + WHOIS socket | ❌ | ✅ F204H | source_type=rir_correlation |
| `social_identity_surface` | ✅ LIVE_ACTIVE | ✅ HTTP (SocialIdentityMinerAdapter) | ❌ | ✅ F204I | tag only |
| `wayback_diff` | ✅ LIVE_ACTIVE | ✅ HTTP (archive.org CDX) | ❌ | ✅ F193A | source_type=wayback_diff |
| `passive_fingerprint` | ✅ LIVE_ACTIVE | ❌ passive | ❌ | probe exists | tag only |
| `passive_tech_stack` | ✅ LIVE_ACTIVE | ❌ passive | ❌ | probe exists | duplicate of passive_fingerprint |
| `evidence_triage` | ✅ LIVE_ACTIVE | ❌ passive | ❌ | probe exists | stats counter only |
| `temporal_archaeology` | ✅ LIVE_ACTIVE | ❌ passive | ❌ | ✅ F202E | source_type=temporal_archaeology |
| `exposure_correlator` | ✅ LIVE_ACTIVE | ❌ passive (correlation only) | ❌ | ✅ F202C | source_type=exposure |
| `identity_stitching` | ✅ LIVE_ACTIVE | ❌ passive | ✅ HEAVY | probe exists | entity extraction |
| `kill_chain_tagging` | ✅ LIVE_ACTIVE | ❌ passive | ❌ | probe exists | tag only |
| `embedding` | ✅ LIVE_ACTIVE_BUT_NOT_VISIBLE | ❌ passive (vector side-effect) | ✅ HEAVY | ❌ no probe | no CanonicalFinding output |
| `sprint_diff` | ✅ LIVE_ACTIVE | ❌ passive | ✅ HEAVY | ❌ no probe | stats counter only |

### 9b. Sidecar Stages

```
Stage 1 (light extraction):   leak_sentinel, passive_fingerprint, passive_tech_stack,
                              evidence_triage, temporal_archaeology, network_intel,
                              banner_grab, ipv6_recon
Stage 2 (correlation):        exposure_correlator, identity_stitching, sprint_diff,
                              rir_correlator, social_identity_surface, wayback_diff
Stage 3 (derived):            kill_chain_tagging, embedding
```

### 9c. Heavy / Active Network Sidecars

- `_HEAVY_SIDECARS: frozenset({"identity_stitching", "embedding", "sprint_diff", "banner_grab", "ipv6_recon"})` — skipped under M1 critical/emergency
- `_ACTIVE_NETWORK_SIDECARS: frozenset({"network_intel", "banner_grab", "ipv6_recon"})` — require explicit active/aggressive profile

---

## 10. Enrichment & Brain Models

### 10a. Multimodal / Forensics (live active)

| Engine | Owner | Canonical Live | M1 Risk | Notes |
|--------|-------|--------------|---------|-------|
| VisionEncoder (image analysis) | `multimodal/analyzer.py` | ✅ LIVE_ACTIVE (opt-in) | ⚠️ MEDIUM | RAM guard at >85%; no VLM |
| VisionOCR (text from images) | `multimodal/analyzer.py` | ✅ LIVE_ACTIVE (opt-in) | ⚠️ MEDIUM | pytesseract + PIL |
| DocumentExtractor (PDF) | `multimodal/analyzer.py` | ✅ LIVE_ACTIVE (opt-in) | LOW | PyPDF2 lazy import |
| ForensicsEnricher | `forensics/enrichment_service.py` | ✅ LIVE_ACTIVE | LOW | metadata + stego + digital_ghost |
| EvidenceTriageCoordinator | `multimodal/evidence_triage.py` | ✅ LIVE_ACTIVE | LOW | MAX_OCR_SNIPPETS=10, MAX_OCR_CHARS=5000 |

### 10b. Memory / Storage

| Engine | Owner | Canonical Live | Offline Only | M1 Risk | Notes |
|--------|-------|--------------|--------------|---------|-------|
| DuckDB (canonical write) | `knowledge/duckdb_store.py` | ✅ LIVE_ACTIVE | ⚠️ read-only | LOW | `async_ingest_findings_batch()` |
| LanceDB (ANN dedup) | `knowledge/lancedb_store.py` | ✅ LIVE_ACTIVE | ⚠️ read-only | ⚠️ ~100MB | `check_ann_duplicate()` in dedup path |
| DuckPGQ Graph | `knowledge/graph_service.py` | ✅ LIVE_ACTIVE | ⚠️ read-only | ⚠️ ~50MB | `upsert_ioc()`, `reset_session()` |
| atomic_storage (claims) | `knowledge/atomic_storage.py` | ✅ LIVE_ACTIVE | ⚠️ read-only | LOW | ClaimClusterIndex |

### 10c. Brain / Models

| Model | Owner | Live Active | M1 Risk | Notes |
|-------|-------|-------------|---------|-------|
| DeepHermes-3B (MLX) | `brain/hermes3_engine.py` | ✅ LIVE_ACTIVE (m1-local) | ⚠️ OOM guard | kv_bits=4, max_kv_size=8192 in generate() |
| ModernBERT (embeddings) | `brain/modernbert_engine.py` | ✅ LIVE_ACTIVE | LOW | batch embedding |
| GLiNER (NER) | `brain/ner_engine.py` | ✅ LIVE_ACTIVE | LOW | entity extraction |
| FlashRank (reranker) | `tools/reranker.py` | ✅ LIVE_ACTIVE (opt-in) | LOW | rerank candidates |
| LLMLingua-2 (prompt compression) | `tools/prompt_compression.py` | ⚠️ DISABLED_BY_POLICY | MEDIUM | Optional dep, not wired in sprint |
| MambaFusion (vision fusion) | `brain/synthesis_runner.py` | ✅ LIVE_ACTIVE | MEDIUM | multimodal orchestration |
| ANER (apple neural) | `brain/ane_embedder.py` | ⚠️ fallback path | LOW | CoreML ANE fallback |

---

## 11. Export / Truth Reports

| Report | Builder | Live Visible | Probe Tests | Notes |
|--------|---------|------------|-------------|-------|
| provider_yield_diagnosis | `_build_provider_yield_diagnosis()` | ✅ LIVE_ACTIVE | ✅ F250C probe | canonical path |
| enrichment_value_delta | `export/formatters.py` | ✅ LIVE_ACTIVE | ✅ F254B probe | value delta yield |
| investigation_packet | `_build_investigation_packet()` | ✅ LIVE_ACTIVE | ✅ F232A probe | next action planning |
| operator_brief | `_build_operator_brief()` | ✅ LIVE_ACTIVE | ❌ no probe | report section |
| truth_trace | `tools/report_truth_trace.py` | ⚠️ OFFLINE_ONLY | ✅ F250A probe | offline diagnostic |
| sprint_markdown_reporter | `export/sprint_markdown_reporter.py` | ✅ LIVE_ACTIVE | ✅ smoke | canonical export |

---

## 12. Replay / Offline Infrastructure

| Module | Status | Wiring | Notes |
|---------|--------|--------|-------|
| `tools/discovery_replay.py` | ⚠️ IMPLEMENTED_NOT_WIRED | No probe tests, no cassette fixtures | offline replay design exists but not activated |
| `replay_research_loop.py` | ⚠️ IMPLEMENTED_NOT_WIRED | No probe tests | standalone research loop |
| CIRCL PDNS replay | ❌ GAP | No fixture, no adapter | `DISCOVERY_OFFLINE_REPLAY_AUDIT.md` design only |
| CT log replay | ❌ GAP | No fixture | offline replay not implemented |
| DuckDB read store | ✅ OFFLINE_ONLY | `knowledge/duckdb_store.py` | read path for offline analysis |
| Graph read path | ✅ OFFLINE_ONLY | `knowledge/graph_service.py` | DuckPGQ read-only queries |

---

## 13. Transport Layers

| Transport | Owner | Canonical Live | M1 Risk | Notes |
|-----------|-------|--------------|---------|-------|
| curl_cffi (JA3 stealth) | `fetching/public_fetcher.py` | ✅ LIVE_ACTIVE | LOW | F202H: primary HTTP seam |
| httpx H2 | `fetching/public_fetcher.py` | ⚠️ OFFLINE_ONLY (F206K gate) | LOW | opt-in via HLEDAC_ENABLE_HTTPX_H2 |
| Tor | `transport/tor_transport.py` | ⚠️ opt-in | MEDIUM ~50MB | stem lazy-loaded |
| I2P | `transport/i2p_transport.py` | ❌ DORMANT | MEDIUM ~50MB | exists, not wired |
| Nym | `transport/nym_transport.py` | ❌ DORMANT | HIGH | 500+ lines, no calls found |
| lightpanda (headless browser) | `tools/lightpanda_pool.py` | ⚠️ DISABLED_BY_POLICY | ⚠️ OOM risk | blocked when MLX model loaded |

---

## 14. Top 5 Wiring Priorities

| # | Capability | Value | Effort | Why Now |
|---|-----------|-------|--------|---------|
| 1 | **CIRCL PDNS passive DNS** | HIGH | MEDIUM | Referenced in F214/F223 lanes but `call_circl_pdns()` missing; highest OSINT ROI |
| 2 | **Sidecar→source_family outcome propagation** | HIGH | LOW | `SidecarRunResult.stored_count` not in `source_family_outcomes`; affects 9/16 sidecar report visibility |
| 3 | **discovery_replay offline fixtures** | MEDIUM | MEDIUM | CIRCL PDNS, CT log, DuckDuckGo lack offline cassettes; blocks reliable offline testing |
| 4 | **RDAP WHOIS adapter** | MEDIUM | MEDIUM | `acquisition_strategy.py` references RDAP lanes but no adapter exists |
| 5 | **LLMLingua-2 wiring** | MEDIUM | LOW | Optional dep in `tools/prompt_compression.py`; wiring into `hermes3_engine.py` reduces LLM latency |

---

## 15. Top 5 M1 Risks

| # | Capability | Risk | Reason |
|---|-----------|------|--------|
| 1 | Lightpanda + MLX | CRITICAL | OOM when both active; blocked by policy but not enforced in all code paths |
| 2 | embedding sidecar + MLX | HIGH | Runs every sprint; produces no CanonicalFinding output — waste of RAM |
| 3 | identity_stitching + MLX | HIGH | HEAVY sidecar; entity extraction is memory-intensive; correctly under governor guard |
| 4 | All 6 active network sidecars | MEDIUM | Run in every sprint even at M1 WARN state |
| 5 | browser rendering | MEDIUM | 2GB+ RAM; blocked by policy but lazy import costs on cold start |

---

## Flags

- `F256A_CAPABILITY_ACTIVATION_MATRIX=true`
- `LIVE_ACTIVE_CAPABILITIES_MAPPED=true`
- `OFFLINE_ONLY_CAPABILITIES_MAPPED=true`
- `DORMANT_CAPABILITIES_MAPPED=true`
- `M1_RISK_CAPABILITIES_MAPPED=true`
- `NO_CODE_CHANGE=true`
- `NO_LIVE_NETWORK=true`
- `F256A_VERIFIED=true`