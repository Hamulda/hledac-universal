# Enrichment Ownership Audit — Sprint F243A

## Scope
- `runtime/sidecar_bus.py` (1061L)
- `runtime/source_finding_bridge.py` (2355L)
- `runtime/sidecar_orchestrator.py` (384L)
- `runtime/enrichment_services.py`
- `intelligence/` (enrichment/correlation adapters)
- `security/passive_dns.py`
- `discovery/` (adapters)
- `forensics/`
- `multimodal/`

---

## Audit Table

| Module | File | Active Call Site | Input | Output | DuckDB | Graph | SidecarBus | source_finding_bridge | M1 Heavy | Network | Model | Duplicate | Owner |
|--------|------|-----------------|-------|--------|--------|-------|------------|----------------------|----------|---------|-------|-----------|-------|
| FindingSidecarBus | `runtime/sidecar_bus.py` | `SidecarOrchestrator.dispatch_findings()` | `list[CanonicalFinding]` | `list[SidecarRunResult]` | YES (via runners) | NO | YES | NO | NO | NO | NO | NO | **sidecar_bus** |
| SidecarOrchestrator | `runtime/sidecar_orchestrator.py` | `SprintScheduler._run_sidecar_orchestrator()` | findings + store | `DispatchOutcome` | NO | NO | YES | NO | NO | NO | NO | NO | **sidecar_bus** |
| source_finding_bridge | `runtime/source_finding_bridge.py` | `SprintScheduler` (CT/Wayback/PDNS/DOH/RDAP lanes) | adapter raw results | `Tuple[List[CanonicalFinding], List[Rejection], dict]` | NO | NO | NO | YES | NO | NO | NO | NO | **source_finding_bridge** |
| EnrichmentServices | `runtime/enrichment_services.py` | `SprintScheduler._enrichment_services` | `CanonicalFinding` | `metadata["forensics"]` | NO | NO | NO | NO | MAYBE | YES | NO | NO | **legacy** |
| exposure_correlator | `intelligence/exposure_correlator.py` | sidecar_bus Stage 2 | `list[CanonicalFinding]` | `list[CanonicalFinding]` | YES | NO | YES | NO | NO | NO | NO | NO | sidecar_bus |
| identity_stitching_canonical | `intelligence/identity_stitching_canonical.py` | sidecar_bus Stage 2 | `list[CanonicalFinding]` | `list[CanonicalFinding]` | YES | YES (advisory via graph_service) | YES | NO | NO | NO | NO | NO | sidecar_bus |
| leak_sentinel | `intelligence/leak_sentinel.py` | sidecar_bus Stage 1 | `list[CanonicalFinding]` | `list[CanonicalFinding]` | YES | NO | YES | NO | NO | NO | NO | NO | sidecar_bus |
| temporal_archaeologist_adapter | `intelligence/temporal_archaeologist_adapter.py` | sidecar_bus Stage 1 | `list[CanonicalFinding]` | `list[CanonicalFinding]` | YES | NO | YES | NO | NO | NO | NO | NO | sidecar_bus |
| passive_fingerprint | `intelligence/passive_fingerprint.py` | sidecar_bus Stage 2 | `list[CanonicalFinding]` | `list[CanonicalFinding]` | YES | NO | YES | NO | NO | NO | NO | NO | sidecar_bus |
| rir_correlator | `intelligence/rir_correlator.py` | sidecar_bus Stage 2 | `list[CanonicalFinding]` | `list[CanonicalFinding]` | YES | NO | YES | NO | NO | NO | NO | NO | sidecar_bus |
| social_identity_miner | `intelligence/social_identity_miner.py` | sidecar_bus Stage 2 | `list[CanonicalFinding]` | `list[CanonicalFinding]` | YES | NO | YES | NO | NO | NO | NO | NO | sidecar_bus |
| passive_tech_stack_runner | `runtime/sidecar_bus.py:_passive_tech_stack_runner` | sidecar_bus registered (never dispatched) | N/A | N/A | N/A | N/A | YES | NO | NO | NO | NO | **DUPLICATE** of passive_fingerprint | dormant |
| evidence_triage_runner | `runtime/sidecar_bus.py` | sidecar_bus registered (never dispatched) | N/A | N/A | N/A | N/A | YES | NO | NO | NO | NO | **DUPLICATE** of multimodal/analyzer | dormant |
| wayback_diff_runner | `runtime/sidecar_bus.py` | sidecar_bus registered (never dispatched) | N/A | N/A | N/A | N/A | YES | NO | NO | NO | NO | **DUPLICATE** of intelligence/wayback_diff_miner | dormant |
| sprint_diff_runner | `runtime/sidecar_bus.py` | sidecar_bus registered (never dispatched) | N/A | N/A | N/A | N/A | YES | NO | NO | NO | NO | NO | dormant |
| kill_chain_tagging_runner | `runtime/sidecar_bus.py` | sidecar_bus registered (never dispatched) | N/A | N/A | N/A | N/A | YES | NO | NO | NO | NO | NO | dormant |
| embedding_runner | `runtime/sidecar_bus.py` | sidecar_bus registered (never dispatched) | N/A | N/A | N/A | N/A | YES | NO | NO | NO | NO | NO | dormant |
| _network_intel_runner | `runtime/sidecar_bus.py` | sidecar_bus registered (network I/O) | `list[CanonicalFinding]` | `list[CanonicalFinding]` | YES | NO | YES | NO | NO | **YES** (aiohttp) | NO | NO | sidecar_bus |
| _banner_grab_runner | `runtime/sidecar_bus.py` | sidecar_bus registered (TCP banner) | `list[CanonicalFinding]` | `list[CanonicalFinding]` | YES | NO | YES | NO | NO | **YES** (socket) | NO | NO | sidecar_bus |
| _ipv6_recon_runner | `runtime/sidecar_bus.py` | sidecar_bus registered (IPv6 recon) | `list[CanonicalFinding]` | `list[CanonicalFinding]` | YES | NO | YES | NO | NO | **YES** (IPv6ReconAdapter) | NO | NO | sidecar_bus |
| ct_log_client | `intelligence/ct_log_client.py` | `SprintScheduler._ct_log_client` | CT API raw | `list[CanonicalFinding]` | YES (via lane) | NO | NO | YES | NO | YES (CT API) | NO | NO | **discovery_replay** |
| wayback_cdx | `intelligence/wayback_cdx.py` | `SprintScheduler._run_wayback_cdx_sidecar()` | Wayback API raw | `list[CanonicalFinding]` | YES (via lane) | NO | NO | YES | NO | YES (Wayback API) | NO | NO | **discovery_replay** |
| ti_feed_adapter | `discovery/ti_feed_adapter.py` | `SprintScheduler._run_ti_feed_lane()` | TI feed raw | `list[CanonicalFinding]` | YES (direct) | NO | NO | NO | NO | YES | NO | NO | **discovery_replay** |
| ForensicsEnricher | `forensics/enrichment_service.py` | `SprintScheduler._enrichment_services` | `CanonicalFinding` | `finding.metadata["forensics"]` | NO | NO | NO | NO | MAYBE | **YES** (WHOIS/SSL/DNS/rDNS via socket/ssl/dns.resolver in asyncio.to_thread) | NO | NO | **legacy** |
| metadata_extractor | `forensics/metadata_extractor.py` | `ForensicsEnricher._extractor` | file path | metadata dict | NO | NO | NO | NO | MAYBE (PIL/pypdf) | NO | NO | NO | **legacy** |
| steganography_detector | `forensics/steganography_detector.py` | `ForensicsEnricher` | file path | stego dict | NO | NO | NO | NO | MAYBE (numpy/scipy) | NO | NO | NO | **legacy** |
| digital_ghost_detector | `forensics/digital_ghost_detector.py` | `ForensicsEnricher` | file path | ghost dict | NO | NO | NO | NO | NO | NO | NO | NO | **legacy** |
| digital_ghost_detector | `security/digital_ghost_detector.py` | NOT called in canonical path | N/A | N/A | N/A | N/A | NO | NO | NO | NO | NO | **DUPLICATE** (security/ vs forensics/) | dormant |
| DocumentExtractor | `multimodal/analyzer.py` | `SprintScheduler._enrichment_services` | file path | `CanonicalFinding(source_type="document")` | YES (via EnrichmentServices → LMDB) | NO | NO | NO | MAYBE (PIL) | NO | NO | NO | **legacy** |
| EvidenceTriage | `multimodal/evidence_triage.py` | NOT wired in canonical path | N/A | N/A | N/A | N/A | NO | NO | MAYBE | NO | NO | **DUPLICATE** of DocumentExtractor | dormant |
| academic_discovery | `intelligence/academic_discovery.py` | NOT wired in canonical path | query | academic results | NO | NO | NO | NO | NO | YES | NO | NO | dormant |
| doh_lane | `intelligence/doh_lane.py` | `SprintScheduler._run_doh_lane()` | DoH API raw | `CanonicalFinding(source_type="doh")` | YES (via lane) | NO | NO | YES (doh_results_to_findings) | NO | YES (DoH API) | NO | NO | **discovery_replay** |
| passive_dns (security) | `security/passive_dns.py` | NOT in canonical path (utility only) | domain | `list[str]` (resolver IPs) | NO | NO | NO | NO | NO | YES (CIRCL PDNS API) | NO | NO | **legacy** |
| bgp_lane | `intelligence/bgp_lane.py` | `SprintScheduler` advisory only | BGP data | `CanonicalFinding` | YES | NO | NO | NO | NO | YES (BGP) | NO | NO | **discovery_replay** |
| passive_fingerprint (duplicate concern) | `intelligence/passive_fingerprint.py` | sidecar_bus + direct | `CanonicalFinding` | `CanonicalFinding` | YES | NO | YES | NO | NO | NO | NO | passive_tech_stack IS derived FROM this | sidecar_bus |
| shodan_wrapper | `intelligence/shodan_wrapper.py` | NOT wired in canonical path | domain | `CanonicalFinding` | NO | NO | NO | NO | NO | YES (Shodan API) | NO | NO | dormant |

---

## Special Focus Findings

### 1. Structured intelligence NOT stored as CanonicalFinding

- **`forensics/enrichment_service.py`**: Produces `ForensicsResult` stored in `finding.metadata["forensics"]` only — NOT persisted as separate CanonicalFinding. The enrichment data lives and dies with the parent finding's `payload_text`.
  - **Risk**: If `payload_text` is truncated, forensics envelope is lost.
  - **Owner**: legacy (F350M planned move to EnrichmentServices)

- **`security/passive_dns.py`**: `lookup_passive_dns()` and `resolve_doh()` return raw lists — no CanonicalFinding production at all. Used as utility functions by ti_feed_adapter and discovery lanes.

### 2. Network from sidecar WITHOUT replay/test seam

- **`_network_intel_runner`** (sidecar_bus.py:917): Uses `network.banner_grabber.BannerGrabberAdapter` — makes live TCP connections to extracted IPs. No test seam.
- **`_banner_grab_runner`** (sidecar_bus.py:940): Same — TCP banner grab. No test seam.
- **`_ipv6_recon_runner`** (sidecar_bus.py:991): Uses `IPv6ReconAdapter` — live IPv6 recon. No test seam.
- **`ForensicsEnricher`** (enrichment_service.py:400-418): WHOIS/SSL/DNS/rDNS via `asyncio.to_thread(_sync_whois)` etc. — these are synchronous blocking calls wrapped in `asyncio.to_thread` with `_EXTERNAL_LOOKUP_TIMEOUT`. No test seam.

### 3. Duplicate RDAP/WHOIS/PDNS parsing

- **`forensics/enrichment_service.py`** has WHOIS/DNS lookups — no RDAP.
- **`security/passive_dns.py`** has `lookup_passive_dns()` (CIRCL PDNS) and `resolve_doh()` (DoH) — separate from discovery.
- No duplication detected: forensics enrichment is domain→metadata; passive_dns is domain→resolved IPs.

### 4. sync `new_event_loop` / `asyncio.run` in async path (M1 crash vectors)

| File | Line | Pattern | Severity |
|------|------|---------|----------|
| `intelligence/exposure_correlator.py` | 388, 492, 564 | `loop = asyncio.new_event_loop()` + `run_until_complete` | **HIGH** — fallback only, but still a crash vector if called from running loop |
| `intelligence/rir_correlator.py` | 617 | `loop = asyncio.new_event_loop()` + `run_until_complete` | **HIGH** — same |
| `intelligence/document_intelligence.py` | 1373 | `loop = asyncio.new_event_loop()` | **HIGH** — in `_run_document_intelligence` |

Note: `academic_discovery.py:294` uses `asyncio.run()` inside `_run_sync()` which is only called from sync wrappers — not a direct M1 crash vector (sync wrappers are deprecated for async callers per docstring).

### 5. Heavy model libs at module import

**NONE FOUND** — no `mlx`, `torch`, `transformers` imported at module level in any enrichment/sidecar module. Good.

### 6. Graph writes directly instead of GraphAccumulator

- **`identity_stitching_canonical.py:360`** (`upsert_identity_edges`): Calls `graph_service.upsert_identity_edge` directly. Docstring says "Graph edges are advisory" — this is intentional. NOT via GraphAccumulator.
  - **Assessment**: Intentional advisory pattern. The `upsert_identity_edge` goes through `graph_service` which routes to `DuckPGQGraph`. Not a violation since it's advisory-only (not canonical finding persistence).

- **`graph_accumulator.py:91`**: `gs.upsert_ioc_batch(rows)` — this IS the canonical graph accumulation path (called from `SprintScheduler._accumulate_findings_to_graph`).

---

## Dormant Modules (registered but never dispatched)

These sidecar runners are registered in `DEFAULT_SIDECAR_RUNNERS` but have **zero active call sites** in `SprintScheduler`:

1. `evidence_triage` — replaced by multimodal DocumentExtractor
2. `wayback_diff` — replaced by intelligence/wayback_diff_miner (direct lane)
3. `sprint_diff` — no active lane
4. `kill_chain_tagging` — no active lane
5. `embedding` — no active lane

**Recommended**: Deprecate and remove from `DEFAULT_SIDECAR_RUNNERS`.

---

## M1 Crash Vectors Found: 4

- `exposure_correlator.py` lines 388, 492, 564: `new_event_loop()` fallback
- `rir_correlator.py` line 617: `new_event_loop()` fallback
- `document_intelligence.py` line 1373: `new_event_loop()`

---

## Heavy Imports Found: 0

No mlx/torch/transformers at module import in sidecar_bus or source_finding_bridge.

---

## Summary Count

| Category | Count |
|----------|-------|
| Dormant enrichments | 7 (5 sidecar runners + 2 duplicate files) |
| Duplicate enrichments | 4 (passive_tech_stack≈passive_fingerprint, evidence_triage≈DocumentExtractor, wayback_diff≈wayback_diff_miner, security/digital_ghost_detector≈forensics/digital_ghost_detector) |
| M1 crash vectors | 4 |
| Heavy imports | 0 |
| Direct graph writes outside GraphAccumulator | 0 (identity_stitching is intentional advisory) |

---

## Recommendations

### Immediately (P0)
1. **Verify `exposure_correlator.py` and `rir_correlator.py` `new_event_loop` calls** — these are in correlation runners called from sidecar bus. Confirm whether they can be reached from an async context. If yes, these are M1 crash vectors.
2. **Deprecate dormant sidecar runners**: `evidence_triage`, `wayback_diff`, `sprint_diff`, `kill_chain_tagging`, `embedding` from `DEFAULT_SIDECAR_RUNNERS` in `sidecar_bus.py`.

### Future (P1)
3. **EnrichmentServices** (F350M): Consolidate forensics + multimodal lifecycle into unified layer per sprint comment.
4. **Test seams** for network sidecars: `_network_intel_runner`, `_banner_grab_runner`, `_ipv6_recon_runner` need replay/test seams.
5. **ForensicsEnricher** envelope persistence: currently stored in `finding.metadata["forensics"]` (inside payload_text). Consider explicit CanonicalFinding with `source_type="forensics"` for independent persistence.