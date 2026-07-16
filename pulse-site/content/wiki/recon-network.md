+++
title = "recon/network/"
weight = 11
description = "<think> Let me analyze this structural context to write a module overview for `recon/network`.  Key observations: 1. This is a Python module with 3 files, 913 total lines (avg 304 lines/file) 2. No ou..."

[extra]
tier = 2
file_count = 3
total_lines = 913
languages = "Python"
parent_path = "recon"
+++

<think>
Let me analyze this structural context to write a module overview for `recon/network`.

Key observations:
1. This is a Python module with 3 files, 913 total lines (avg 304 lines/file)
2. No outgoing dependencies and no incoming dependencies detected
3. It's Tier 2
4. Two main components:
 - `bgp_monitor.py` (637 lines) - BGP monitoring and enrichment
 - `passive_fingerprint.py` (275 lines) - Passive fingerprinting client

The module provides:
- BGP monitoring capabilities with RIPE Stat API integration
- Passive fingerprinting that queries multiple external sources (Shodan, Grey Noise, CIRCL PDNS, Virus Total, Security Trails)
- IP extraction from text
- Various enrichment adapters

Key symbols show high fan-out for the `query` function (436 references), suggesting it's widely used internally even though no dependencies are detected in the structural context.

Let me craft a concise overview that:
- States what the module DOES first
- Describes its purpose and responsibilities
- Explains its architectural role
- Notes complexity/scale
- Doesn't start with "The X module consists of..."
- Varies sentence structure
- 4-8 sentences
</think>

This module enriches reconnaissance findings with real-time network intelligence by querying external threat and infrastructure data sources. It operates in two distinct modes: passive fingerprinting that aggregates IP and domain data from services like Shodan InternetDB, Grey Noise, CIRCL PDNS, Virus Total, and Security Trails, and live BGP monitoring that enriches IP addresses and ASNs with routing context from RIPE Stat API. The BGP enrichment layer handles ASN resolution, prefix information, and whois data with built-in safeguards—private IP addresses are never sent to external APIs, requests are rate-limited, and individual lookups timeout at 30 seconds with soft failure handling. A caching mechanism with configurable TTL prevents redundant lookups within the same async context, and a Rust-based fast path accelerates public IPv4 extraction from arbitrary text. Despite its compact footprint of three files totaling under a thousand lines, the module exposes a heavily-used query entry point referenced across hundreds of dependent modules, giving it significant blast radius if modified. Its lack of incoming dependencies in the structural snapshot may indicate it serves as a foundational intelligence layer consumed transitively through other wrappers, rather than being called directly by application code.

## Structure

| Language | Files |
|---|---|
| Python | 3 |

### Largest Files

- `bgp_monitor.py` (637 lines)
- `passive_fingerprint.py` (275 lines)
- `__init__.py` (1 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>query</code> (Function) in passive_fingerprint.py — referenced in 436 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _bgp.py, _catalog.py, _debug_test.py +411 more</li></ul>
</li>
<li>
<p><code>PassiveFingerprint</code> (Class) in passive_fingerprint.py — referenced in 5 files</p>
<details><summary>Multi-source passive fingerprinting client.</summary>
<div class="doc-comment">
<p>Multi-source passive fingerprinting client.</p>
<p></p>
<p>Methods (all async):</p>
<p>- lookup_ip(ip)    → dict with tags, ports, cpes, hostnames, etc.</p>
<p>- lookup_domain(domain) → dict with subdomains, emails, etc.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, network_intelligence.py</li></ul>
</li>
<li>
<p><code>PassiveFingerprintAdapter</code> (Class) in passive_fingerprint.py — referenced in 5 files</p>
<details><summary>Passive fingerprint adapter for sidecar runners.</summary>
<div class="doc-comment">
<p>Passive fingerprint adapter for sidecar runners.</p>
<p>Wraps PassiveFingerprint, returns CanonicalFinding-compatible dicts.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, network_intelligence.py</li></ul>
</li>
<li>
<p><code>monitor_bgp</code> (Function) in bgp_monitor.py — referenced in 5 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, network_intelligence.py, ti_feed_adapter.py</li></ul>
</li>
<li>
<p><code>bgp_enrich_to_canonical</code> (Function) in bgp_monitor.py — referenced in 4 files</p>
<details><summary>BGP enrichment adapter — mapuje BGP monitor output na CanonicalFinding.</summary>
<div class="doc-comment">
<p>BGP enrichment adapter — mapuje BGP monitor output na CanonicalFinding.</p>
<p></p>
<p>Args:</p>
<p>ip_or_asn: IP adresa nebo ASN (např. "8.8.8.8" nebo "AS15169")</p>
<p>query: kontextový dotaz pro BGP lookup</p>
<p>Returns:</p>
<p>list[CanonicalFinding] s source_type="bgp_enrichment"</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, sprint_scheduler_v1_archived.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (31)</summary>
<ul>
<li><code>monitor_bgp</code> (bgp_monitor.py)</li>
<li><code>monitor_bgp_as_findings</code> (bgp_monitor.py)</li>
<li><code>enrich_ip_as_finding</code> (bgp_monitor.py)
<details><summary>F234: Enrich a single IP with live BGP data from RIPE Stat API.</summary>
<div class="doc-comment">
<p>F234: Enrich a single IP with live BGP data from RIPE Stat API.</p>
<p></p>
<p>Invariants (F234):</p>
<p>- RFC1918/loopback IPs are NEVER sent to RIPE (gate: extract_public_ips_from_text)</p>
<p>- Max 20 IPs per sprint (enforced by caller)</p>
<p>- 30s timeout per IP</p>
<p>- Fail-soft: any error → return []</p>
<p></p>
<p>RIPE Stat API (no auth required):</p>
<p>- GET prefix-overview → ASN, prefix, holder</p>
<p>- GET whois (per ASN) → org name, country, abuse contact</p>
<p></p>
<p>Returns:</p>
<p>list[CanonicalFinding] with source_type="bgp_ripe_stat", confidence=0.88</p>
</div>
</details>
</li>
<li><code>_stream_events</code> (bgp_monitor.py) — <span class="doc-comment-inline">Async wrapper around sync pybgpstream iteration.</span></li>
<li><code>_to_canonical</code> (bgp_monitor.py)</li>
<li><code>_lookup</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Generic lookup with cache, rate limit, circuit breaker.</span></li>
<li><code>extract_public_ips_from_text</code> (bgp_monitor.py)
<details><summary>Extract public IPv4 addresses from text. Filters RFC1918/loopback/link-local.</summary>
<div class="doc-comment">
<p>Extract public IPv4 addresses from text. Filters RFC1918/loopback/link-local.</p>
<p></p>
<p>F234 INVARIANT: Private IPs (RFC1918, loopback, link-local) are NEVER sent to</p>
<p>BGP enrichment. Max 20 IPs per sprint (dedup + cap).</p>
<p></p>
<p>Rust fast-path: uses pre-compiled OnceCell regex via hledac_rust_extensions.</p>
<p>Python fallback: uses module-level _IPV4_PATTERN.</p>
</div>
</details>
</li>
<li><code>query</code> (passive_fingerprint.py)</li>
<li><code>bgp_enrich_to_canonical</code> (bgp_monitor.py)
<details><summary>BGP enrichment adapter — mapuje BGP monitor output na CanonicalFinding.</summary>
<div class="doc-comment">
<p>BGP enrichment adapter — mapuje BGP monitor output na CanonicalFinding.</p>
<p></p>
<p>Args:</p>
<p>ip_or_asn: IP adresa nebo ASN (např. "8.8.8.8" nebo "AS15169")</p>
<p>query: kontextový dotaz pro BGP lookup</p>
<p>Returns:</p>
<p>list[CanonicalFinding] s source_type="bgp_enrichment"</p>
</div>
</details>
</li>
<li><code>_parse_as_path</code> (bgp_monitor.py) — <span class="doc-comment-inline">Normalize AS path to space-separated string.</span></li>
<li><code>_get_rate_limit_lock</code> (passive_fingerprint.py)
<details><summary>Lazy asyncio.Lock getter — ContextVar ensures one lock per async context.</summary>
<div class="doc-comment">
<p>Lazy asyncio.Lock getter — ContextVar ensures one lock per async context.</p>
<p></p>
<p>Fixes ISSUE-014: asyncio.Lock() at module import crashes on macOS because</p>
<p>asyncio.Lock() captures the current event loop at creation time. By storing</p>
<p>the lock in a ContextVar and creating it lazily inside the first async call,</p>
<p>we guarantee an event loop exists at creation time.</p>
</div>
</details>
</li>
<li><code>lookup_ip</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Look up an IP across all available sources in parallel.</span></li>
<li><code>lookup_domain</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Look up a domain across all available sources in parallel.</span></li>
<li><code>_get_rate_limiter</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Get or create a per-source rate limiter Semaphore for the current async context.</span></li>
<li><code>get</code> (passive_fingerprint.py)</li>
<li><code>set</code> (passive_fingerprint.py)</li>
<li><code>_get_fp_cache</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Get or create the ContextVar-backed _FPCache for the current async context.</span></li>
<li><code>_get_source_rate_limiters</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Get the ContextVar-backed source rate limiters dict for the current context.</span></li>
<li><code>securitytrails</code> (passive_fingerprint.py) — <span class="doc-comment-inline">SecurityTrails — requires API key, fail-soft if not configured.</span></li>
<li><code>_is_ip</code> (passive_fingerprint.py)</li>
<li><code>_ensure_session</code> (passive_fingerprint.py)</li>
<li><code>shodan_internetdb</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Shodan InternetDB — free, no API key. Returns tags, ports, cpes, hostnames.</span></li>
<li><code>greynoise_community</code> (passive_fingerprint.py) — <span class="doc-comment-inline">GreyNoise Community — free tier, no API key. Returns classification, tags, metadata.</span></li>
<li><code>circl_pdns</code> (passive_fingerprint.py) — <span class="doc-comment-inline">CIRCL Passive DNS — free, no API key. Returns A/AAAA/CNAME records.</span></li>
<li><code>virustotal</code> (passive_fingerprint.py) — <span class="doc-comment-inline">VirusTotal v3 — free tier, API key optional. Returns last_analysis_stats.</span></li>
<li><code>__init__</code> (passive_fingerprint.py)</li>
<li><code>close</code> (passive_fingerprint.py)</li>
<li><code>_key</code> (passive_fingerprint.py)</li>
<li><code>__init__</code> (passive_fingerprint.py)</li>
<li><code>__init__</code> (passive_fingerprint.py)</li>
<li><code>close</code> (passive_fingerprint.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (3)</summary>
<ul>
<li><code>PassiveFingerprint</code> (passive_fingerprint.py)
<details><summary>Multi-source passive fingerprinting client.</summary>
<div class="doc-comment">
<p>Multi-source passive fingerprinting client.</p>
<p></p>
<p>Methods (all async):</p>
<p>- lookup_ip(ip)    → dict with tags, ports, cpes, hostnames, etc.</p>
<p>- lookup_domain(domain) → dict with subdomains, emails, etc.</p>
</div>
</details>
</li>
<li><code>PassiveFingerprintAdapter</code> (passive_fingerprint.py)
<details><summary>Passive fingerprint adapter for sidecar runners.</summary>
<div class="doc-comment">
<p>Passive fingerprint adapter for sidecar runners.</p>
<p>Wraps PassiveFingerprint, returns CanonicalFinding-compatible dicts.</p>
</div>
</details>
</li>
<li><code>_FPCache</code> (passive_fingerprint.py) — <span class="doc-comment-inline">TTL-cached fingerprint lookups, bounded by MAX_FP_CACHE_SIZE.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (18)</summary>
<ul>
<li><code>_lookup</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Generic lookup with cache, rate limit, circuit breaker.</span></li>
<li><code>query</code> (passive_fingerprint.py)</li>
<li><code>lookup_ip</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Look up an IP across all available sources in parallel.</span></li>
<li><code>lookup_domain</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Look up a domain across all available sources in parallel.</span></li>
<li><code>get</code> (passive_fingerprint.py)</li>
<li><code>set</code> (passive_fingerprint.py)</li>
<li><code>securitytrails</code> (passive_fingerprint.py) — <span class="doc-comment-inline">SecurityTrails — requires API key, fail-soft if not configured.</span></li>
<li><code>_ensure_session</code> (passive_fingerprint.py)</li>
<li><code>shodan_internetdb</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Shodan InternetDB — free, no API key. Returns tags, ports, cpes, hostnames.</span></li>
<li><code>greynoise_community</code> (passive_fingerprint.py) — <span class="doc-comment-inline">GreyNoise Community — free tier, no API key. Returns classification, tags, metadata.</span></li>
<li><code>circl_pdns</code> (passive_fingerprint.py) — <span class="doc-comment-inline">CIRCL Passive DNS — free, no API key. Returns A/AAAA/CNAME records.</span></li>
<li><code>virustotal</code> (passive_fingerprint.py) — <span class="doc-comment-inline">VirusTotal v3 — free tier, API key optional. Returns last_analysis_stats.</span></li>
<li><code>__init__</code> (passive_fingerprint.py)</li>
<li><code>close</code> (passive_fingerprint.py)</li>
<li><code>_key</code> (passive_fingerprint.py)</li>
<li><code>__init__</code> (passive_fingerprint.py)</li>
<li><code>__init__</code> (passive_fingerprint.py)</li>
<li><code>close</code> (passive_fingerprint.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (9)</summary>
<ul>
<li><code>_IPV4_PATTERN</code> (bgp_monitor.py)</li>
<li><code>_PRIVATE_RANGES</code> (bgp_monitor.py)</li>
<li><code>BGP_EVENT_TYPES</code> (bgp_monitor.py)</li>
<li><code>_RIPE_PREFIX_URL</code> (bgp_monitor.py)</li>
<li><code>_RIPE_WHOIS_URL</code> (bgp_monitor.py)</li>
<li><code>_RIPE_TIMEOUT</code> (bgp_monitor.py)</li>
<li><code>MAX_FP_CACHE_SIZE</code> (passive_fingerprint.py)</li>
<li><code>FP_CACHE_TTL_S</code> (passive_fingerprint.py)</li>
<li><code>FP_SOURCE_TIMEOUT_S</code> (passive_fingerprint.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 913 |
| Avg lines/file | 304 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

