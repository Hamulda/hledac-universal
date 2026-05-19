# OSINT Capability Coverage Audit — 2026-05-18

## Scope
- `discovery/` — public/Darkweb discovery adapters
- `intelligence/` — TI feeds, leak sentinels, academic search
- `pipeline/` — live sprint pipelines
- `transport/` — Tor/I2P/Nym/stealth transports
- `tools/` — capability utilities
- `runtime/acquisition_strategy.py` — lane definitions
- `docs/LOCAL_OSINT_CAPABILITY_MATRIX.md` — baseline reference
- `pyproject.toml` extras

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

## 8. Summary

| Category | Status |
|----------|--------|
| Implemented & wired | 17 capabilities |
| Implemented but dormant | 5 files (commoncrawl, stealth_crawler, i2p, nym, hnsw_builder) |
| Referenced but missing | 2 (CIRCL PDNS, RDAP WHOIS, Onion discovery) |
| Transport only (not discovery) | 3 (i2p, nym, tor transport) |

**Key finding:** The sprint F214/F223 nonfeed pipeline references CIRCL PDNS and onion discovery as eligible lanes but neither is implemented. This is the primary capability gap.