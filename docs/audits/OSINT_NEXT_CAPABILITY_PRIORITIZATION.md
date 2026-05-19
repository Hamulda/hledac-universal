# OSINT Next-Capability Prioritization — 2026-05-18

## Context

Source: `docs/audits/OSINT_CAPABILITY_COVERAGE_AUDIT.md` capability matrix (17 implemented, 5 dormant, 3 referenced-but-missing).

Goal: pick the highest-value OSINT capability for M1-local mode, constrained to:
- No browser
- No Torch
- No OCR
- Low RAM
- High OSINT gain
- Fail-soft
- Testable offline via fixtures

---

## Shortlist: Top 5 Unimplemented Capabilities

| # | Capability | Where Referenced | Gap Type |
|---|-----------|-----------------|----------|
| 1 | **CIRCL PDNS passive DNS** | `nonfeed_candidate_ledger.py`, `source_finding_bridge.py` | Referenced but missing — F214/F223 lanes reference `passive_dns_results_to_findings` but no `call_circl_pdns()` exists |
| 2 | **RDAP WHOIS** | `runtime/acquisition_strategy.py` (RDAP lanes) | No `call_rdap()` / `async_whois()` implementation anywhere in discovery/ or tools/ |
| 3 | **Common Crawl wet API** | `tools/commoncrawl_adapter.py` exists (dormant, ~200 lines) | File exists but not wired into any source tier or pipeline |
| 4 | **Onion discovery** | `live_public_pipeline.py` imports "onion_discovery", no file found | Referenced in source_tier and sprint_exporter but no `onion_discovery.py` |
| 5 | **I2P eepProxy gateway** | `transport/i2p_transport.py` exists (280+ lines, dormant) | I2P transport exists but not wired as discovery endpoint |

---

## Scoring Matrix

| Criterion | CIRCL PDNS | RDAP WHOIS | Common Crawl | Onion Discovery | I2P Gateway |
|-----------|-----------|------------|--------------|-----------------|-------------|
| **Dependency impact** | `default` (aiohttp) — zero new extras | `default` (aiohttp) — zero new extras | `default` (aiohttp) — zero new extras | `default` + Tor stem — minimal new deps | `transport` extra already has stem |
| **Memory impact** | ~20MB | ~20MB | ~20MB | ~50MB (Tor daemon) | ~50MB (I2P daemon) |
| **M1 safety** | ✅ no GPU, no MLX | ✅ no GPU, no MLX | ✅ no GPU, no MLX | ⚠️ Tor daemon ~50MB | ⚠️ I2P daemon ~50MB |
| **Testability** | ✅ fixture OK (static JSON from CIRCL), offline unit test | ✅ fixture OK (RDAP responses are standard JSON), offline unit test | ⚠️ large fixture required (wet file) | ⚠️ requires Tor subprocess or mock | ⚠️ requires I2P daemon or mock |
| **False positive risk** | LOW — structured DNS data, domain-normalized | LOW — RDAP is standardized WHOIS replacement | MEDIUM — wet files noisy, many non-resolving snapshots | HIGH — onion sites go dark, high churn | LOW — I2P destinations stable |
| **Integration point** | `nonfeed_candidate_ledger.py` lane planner (F214 already references it) | `acquisition_strategy.py` RDAP lane, `runtime/` | `discovery/` adapter, wire into `source_tier` | `discovery/` new module, `live_public_pipeline.py` source_tier | `transport/` already has i2p_transport, needs discovery seam |
| **OSINT gain** | HIGH — domain→current/recent IP pivot, historical DNS records | HIGH — domain→registrant/org pivot, registration age | MEDIUM — historical snapshots beyond Wayback CDX, large but noisy | MEDIUM — discover new onion sites (currently only follows known links) | LOW — niche, I2P destinations rare in OSINT pivot flows |
| **Fail-soft** | ✅ CIRCL free, no API key, rate-limited but stable | ✅ RDAP free for basic queries, no API key | ✅ HTTP errors → empty, cached fallback | ✅ Tor failures → skip, no API key | ✅ I2P failures → skip, gateway unstable |
| **Existing pattern** | ✅ mirrors `crtsh_adapter.py` exactly — same `async_get_aiohttp_session()` + `checked_aiohttp_get()` + cache | ✅ similar to crtsh_adapter pattern | ⚠️ adapter exists but needs wiring | ❌ no pattern — would need new discovery module | ⚠️ transport exists, needs discovery seam |

---

## Detailed Analysis

### 1. CIRCL PDNS — RECOMMENDED (Score: 9/10)

**Dependency impact:** Zero — uses `default` aiohttp via canonical `async_get_aiohttp_session()`. No new extras.

**Memory impact:** ~20MB. Single HTTP call per query; JSON response; no streaming, no parsing overhead.

**Testability:** High. Static JSON fixture from CIRCL API (e.g., `pdns.circl.lu/lookup/a/example.com`) is deterministic, offline-testable. Example fixture structure:
```json
{
  "query": "AAAA", "answer": [
    {"rrtype": "A", "rdata": "93.184.216.34", "count": 5, "first_seen": "2024-01-01", "last_seen": "2025-01-01"}
  ]
}
```

**False positive risk:** LOW. CIRCL PDNS returns structured DNS records — authoritative, time-bounded, normalized domain/IP. No scraping noise.

**Integration point:** Already referenced in `nonfeed_candidate_ledger.py` (F214) — `compute_lane_eligibility` already references `passive_dns` as an eligible lane. `source_finding_bridge.py` has `passive_dns_results_to_findings` converter with no caller. Implementation path:
1. Create `circl_pdns_adapter.py` in `discovery/` (mirrors `crtsh_adapter.py` pattern)
2. Add `async_search_circl_pdns(domain, timeout_s=5.0)` → `DiscoveryBatchResult`
3. Wire into `nonfeed_candidate_ledger.py` lane planner
4. `passive_dns_results_to_findings` converter becomes the actual caller

**OSINT gain:** HIGH. CIRCL PDNS provides **current and recent DNS records** — critical for domain→IP pivot when crt.sh only shows historical certs. Enables pivot chain: domain → current IPs → host enumeration. Free, no API key, rate-limited but stable (~1 req/s).

**Why not higher score:** Not 10/10 because it is still a network call (not purely local), and the CIRCL endpoint has rate limits.

**Implementation estimate:** ~250 lines, 1 adapter + 1 lane wire + 1 converter activation. Low risk.

---

### 2. RDAP WHOIS — Score: 7/10

**Dependency impact:** Zero — `default` aiohttp.

**Memory impact:** ~20MB.

**Testability:** High. RDAP responses are standardized JSON (RFC 7483). Fixture-friendly.

**False positive risk:** LOW. RDAP is standardized; responses include registration status, nameservers, dates.

**Integration point:** `runtime/acquisition_strategy.py` already defines RDAP lanes. No `call_rdap()` exists anywhere. Would add new `rdap_adapter.py` in `discovery/`. Endpoints: `https://rdap.verisign.com/` (com/net), `https://rdap.org/` (multi-TLD).

**OSINT gain:** HIGH. RDAP provides domain→registrant, registration dates, nameservers — critical for pivot from domain to org/registrant info. No API key for basic queries.

**Why lower than CIRCL PDNS:** Less referenced in existing F214/F223 lanes (F214 has passive_dns, not RDAP explicitly). RDAP is more for brand/investigative OSINT; CIRCL PDNS directly enables the domain→IP pivot in the nonfeed pipeline.

---

### 3. Common Crawl wet API — Score: 5/10

**Dependency impact:** Zero — `default` aiohttp. Adapter already exists at `tools/commoncrawl_adapter.py`.

**Memory impact:** ~20MB.

**Testability:** MEDIUM — wet files are large (GB), not fixture-friendly. Would need to mock the HTTP response with a small sample.

**False positive risk:** MEDIUM. Common Crawl has noisy, non-resolving snapshots. Many URLs in wet files are dead. Requires significant post-processing.

**Integration point:** Adapter exists but not wired. Would need to add to `source_tier` and create a lane.

**OSINT gain:** MEDIUM. Provides historical snapshots beyond Wayback CDX. Useful for old content discovery but high noise ratio.

**Why below RDAP:** Dormant adapter needs revival + wiring; noisier data; lower OSINT pivot value per query.

---

### 4. Onion Discovery — Score: 4/10

**Dependency impact:** Minimal — `default` + Tor stem (already in `transport` extra).

**Memory impact:** ~50MB (Tor daemon overhead).

**Testability:** MEDIUM — requires Tor subprocess or mock. More complex than HTTP adapters.

**False positive risk:** HIGH. Onion sites go dark frequently. Churn is very high. Most discovered onions are defunct.

**Integration point:** `live_public_pipeline.py` already imports "onion_discovery" but no file exists. Would need new module.

**OSINT gain:** MEDIUM for specific use cases (darknet OSINT). Not a general-purpose pivot.

**Why below Common Crawl:** High maintenance cost (Tor daemon), high false positive rate, specialized use case.

---

### 5. I2P eepProxy Gateway — Score: 3/10

**Dependency impact:** `transport` extra already has stem — minimal new deps.

**Memory impact:** ~50MB (I2P daemon overhead).

**Testability:** MEDIUM — requires I2P daemon or mock.

**False positive risk:** LOW. I2P destinations are relatively stable.

**Integration point:** `transport/i2p_transport.py` exists (280+ lines) but not wired as discovery endpoint. Would need discovery seam on top of transport.

**OSINT gain:** LOW for general OSINT. I2P destinations are niche, rarely appear in pivot flows for external OSINT.

**Why last:** Requires daemon, niche use case, high memory cost for low OSINT gain in typical pivot flows.

---

## Recommendation

### Primary: Sprint F229 — CIRCL PDNS Passive DNS

**Why CIRCL PDNS is the best next capability:**

1. **Already referenced by F214/F223** — `nonfeed_candidate_ledger.py` has `compute_lane_eligibility` for passive DNS, `source_finding_bridge.py` has `passive_dns_results_to_findings` converter — but neither is implemented. Completing this wiring is the highest-leverage next step.

2. **Zero new dependencies** — uses `default` aiohttp via canonical `async_get_aiohttp_session()` + `checked_aiohttp_get()`, same pattern as `crtsh_adapter.py`.

3. **Low memory** — ~20MB, no GPU, no MLX, M1-safe.

4. **High OSINT pivot value** — domain → current/recent IP chain that crt.sh cannot provide (historical certs only). Completes the domain enumeration triangle: CT (historical subdomains) + PDNS (current IPs) + RDAP (registrant).

5. **Free, no API key** — CIRCL PDNS is rate-limited but stable, no authentication overhead.

6. **Easily testable** — static JSON fixtures, offline unit tests, same pattern as crtsh_adapter probe tests.

7. **Fail-soft throughout** — timeout → empty, HTTP errors → empty, rate-limited → cooldown.

**Scope for implementation:**
- `discovery/circl_pdns_adapter.py` (~250 lines, mirrors crtsh_adapter.py)
- Wire into `nonfeed_candidate_ledger.py` lane planner
- Activate `source_finding_bridge.py::passive_dns_results_to_findings`
- Add probe tests (~15-20 tests, same structure as F217D/F219E crtsh tests)
- Add fixture with static CIRCL JSON response

**Estimated probe tests:** 20 (pattern mirrors crtsh_adapter: valid response, empty response, timeout, HTTP 5xx, parse error, domain normalization, cache fallback, cooldown states).

---

### Secondary: Sprint F230 — RDAP WHOIS

**Why second:** Completes the domain intelligence triad. After CIRCL PDNS is wired, RDAP adds registrant/org pivot. Same pattern, same low cost.

**Deprioritize:** Common Crawl, Onion Discovery, I2P — higher cost, lower general OSINT value, more complex testability.

---

## Output

Written to: `docs/audits/OSINT_NEXT_CAPABILITY_PRIORITIZATION.md`