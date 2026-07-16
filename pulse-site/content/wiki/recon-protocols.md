+++
title = "recon/protocols/"
weight = 12
description = "<think> Let me analyze the structural context for the `recon/protocols` module:  **What it does:** - Computes JARM (JAvascript Remote Make) hashes for TLS fingerprinting of domains - Handles the Gemin..."

[extra]
tier = 2
file_count = 3
total_lines = 1049
languages = "Python"
parent_path = "recon"
+++

<think>
Let me analyze the structural context for the `recon/protocols` module:

**What it does:**
- Computes JARM (JAvascript Remote Make) hashes for TLS fingerprinting of domains
- Handles the Gemini protocol (an alternative internet protocol)
- Has no dependencies itself but is referenced by 84 files for the fingerprint function

**Key characteristics:**
- 3 Python files, 1049 total lines (avg 349 lines/file)
- Self-contained with no outgoing dependencies
- The `fingerprint` function is referenced by 84 files (high fan-out for a leaf, but the module itself is a hub for those 84 files that depend on it)
- Actually wait, the dependency section says "No incoming dependencies detected" but the `fingerprint` function is referenced in 84 files. This is contradictory - let me re-read.

Looking at the dependencies:
- Outgoing deps: 0
- Incoming deps: Not explicitly listed as detected

But the key symbols show:
- `fingerprint` is referenced in 84 files
- `_compute_jarm_async` is referenced in 2 files
- `extract_gemini_links` is referenced in 2 files
- etc.

So this module has high fan-out (many things reference it), but the dependency tracking might not be picking it up correctly. The `fingerprint` function being referenced 84 times is significant.

**Module purpose:**
1. JARM TLS fingerprinting - computes cryptographic fingerprints of TLS servers
2. Gemini protocol support - crawls/searches the Gemini space (an alternative protocol)

**Architectural role:**
- A leaf module (no outgoing deps) that provides specialized protocol capabilities
- High utility value with the `fingerprint` function being used across many files
- Self-contained - doesn't depend on other project modules

**Complexity:**
- Moderate size for a specialized module


- Dense functionality across its files
- JARM fingerprinting dominates the codebase, with the largest file focusing on cryptographic protocol analysis

I'll draft a concise overview emphasizing its specialized protocol intelligence capabilities:

This module provides advanced protocol reconnaissance by generating unique TLS fingerprints and supporting the emerging Gemini protocol ecosystem. Its self-contained design allows independent protocol exploration without external module dependencies. The cryptographic fingerprinting technique enables deep server identification and characterization, serving as a critical intelligence gathering tool for network reconnaissance.
</think>

This module provides protocol-specific reconnaissance capabilities for TLS fingerprinting and the Gemini alternative internet protocol. Its primary function computes JARM (JAvascript Remote Make) hashes by sending ten carefully crafted TLS Client Hello packets with varying configurations and hashing the server responses, enabling identification of TLS server implementations independent of certificate details. A secondary focus supports the Gemini protocol, including URL parsing, capsule crawling, and gemtext link extraction for indexing the decentralized Gemini space. With 27 functions and only three Python files averaging 349 lines each, this is a dense, focused module that packs significant capability into a compact footprint. The absence of outgoing dependencies marks it as a leaf module within the system's architecture, while the `fingerprint` function alone is referenced across 84 files, demonstrating its value as a foundational utility for TLS characterization across the codebase. Caching via SQLite preserves computed hashes to avoid redundant network probes, and both JARM computation and Gemini transport include asynchronous variants for efficient batch processing.

## Structure

| Language | Files |
|---|---|
| Python | 3 |

### Largest Files

- `jarm_fingerprinter.py` (570 lines)
- `gemini_transport.py` (478 lines)
- `__init__.py` (1 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>fingerprint</code> (Function) in jarm_fingerprinter.py — referenced in 84 files</p>
<details><summary>Return JARM hash for domain, using cache if available.</summary>
<div class="doc-comment">
<p>Return JARM hash for domain, using cache if available.</p>
<p></p>
<p>Args:</p>
<p>domain: Target domain name</p>
<p>port: Target port (default 443)</p>
<p></p>
<p>Returns:</p>
<p>62-character JARM hash or None if unavailable</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, _body_hash.py, _domain_protocol.py, _factories.py, _models.py +75 more</li></ul>
</li>
<li>
<p><code>_compute_jarm_async</code> (Function) in jarm_fingerprinter.py — referenced in 2 files</p>
<details><summary>Async JARM computation - uses asyncio.sleep instead of blocking time.sleep.</summary>
<div class="doc-comment">
<p>Async JARM computation - uses asyncio.sleep instead of blocking time.sleep.</p>
<p></p>
<p>Sends 10 TLS Client Hello packets and computes the JARM hash.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: test_f_asyncio_run_audit.py</li></ul>
</li>
<li>
<p><code>extract_gemini_links</code> (Function) in gemini_transport.py — referenced in 2 files</p>
<details><summary>Extract links from Gemini gemtext format.</summary>
<div class="doc-comment">
<p>Extract links from Gemini gemtext format.</p>
<p></p>
<p>Args:</p>
<p>gemtext: Gemini markup text</p>
<p></p>
<p>Returns:</p>
<p>List of (url, label) tuples</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: test_alt_protocols.py</li></ul>
</li>
<li>
<p><code>geminispace_to_findings</code> (Function) in gemini_transport.py — referenced in 2 files</p>
<ul><li class="ref-list">Referenced by: alternative_protocol_fetcher.py</li></ul>
</li>
<li>
<p><code>parse_gemini_url</code> (Function) in gemini_transport.py — referenced in 2 files</p>
<details><summary>Parse Gemini URL into components.</summary>
<div class="doc-comment">
<p>Parse Gemini URL into components.</p>
<p></p>
<p>Args:</p>
<p>url: Gemini URL (gemini://host[:port]/path)</p>
<p></p>
<p>Returns:</p>
<p>(host, port, selector)</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: test_alt_protocols.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (27)</summary>
<ul>
<li><code>_build_client_hello</code> (jarm_fingerprinter.py)</li>
<li><code>_fetch_gemini_tcp</code> (gemini_transport.py)</li>
<li><code>_build_payloads</code> (jarm_fingerprinter.py)
<details><summary>Build the 10 TLS Client Hello payloads.</summary>
<div class="doc-comment">
<p>Build the 10 TLS Client Hello payloads.</p>
<p></p>
<p>Returns list of 10 raw TLS Client Hello bytes.</p>
</div>
</details>
</li>
<li><code>crawl_capsule</code> (gemini_transport.py)</li>
<li><code>geminispace_to_findings</code> (gemini_transport.py)</li>
<li><code>_extract_cipher_version</code> (jarm_fingerprinter.py)
<details><summary>Extract cipher suite and TLS version from TLS response.</summary>
<div class="doc-comment">
<p>Extract cipher suite and TLS version from TLS response.</p>
<p></p>
<p>Returns 6-character hex string (4 chars for cipher + 2 for version).</p>
</div>
</details>
</li>
<li><code>search_geminispace</code> (gemini_transport.py)
<details><summary>Search Gemini capsules via Kennedy search engine.</summary>
<div class="doc-comment">
<p>Search Gemini capsules via Kennedy search engine.</p>
<p></p>
<p>Args:</p>
<p>query: Search query string</p>
<p></p>
<p>Returns:</p>
<p>List of GeminiFinding matching the query</p>
</div>
</details>
</li>
<li><code>_compute_jarm_async</code> (jarm_fingerprinter.py)
<details><summary>Async JARM computation - uses asyncio.sleep instead of blocking time.sleep.</summary>
<div class="doc-comment">
<p>Async JARM computation - uses asyncio.sleep instead of blocking time.sleep.</p>
<p></p>
<p>Sends 10 TLS Client Hello packets and computes the JARM hash.</p>
</div>
</details>
</li>
<li><code>_send_tls_packet</code> (jarm_fingerprinter.py)</li>
<li><code>fetch_capsule_content</code> (gemini_transport.py)</li>
<li><code>parse_gemini_url</code> (gemini_transport.py)
<details><summary>Parse Gemini URL into components.</summary>
<div class="doc-comment">
<p>Parse Gemini URL into components.</p>
<p></p>
<p>Args:</p>
<p>url: Gemini URL (gemini://host[:port]/path)</p>
<p></p>
<p>Returns:</p>
<p>(host, port, selector)</p>
</div>
</details>
</li>
<li><code>fingerprint</code> (jarm_fingerprinter.py)
<details><summary>Return JARM hash for domain, using cache if available.</summary>
<div class="doc-comment">
<p>Return JARM hash for domain, using cache if available.</p>
<p></p>
<p>Args:</p>
<p>domain: Target domain name</p>
<p>port: Target port (default 443)</p>
<p></p>
<p>Returns:</p>
<p>62-character JARM hash or None if unavailable</p>
</div>
</details>
</li>
<li><code>get_capsule_index</code> (gemini_transport.py)
<details><summary>Fetch list of known Gemini capsules from circumlunar.</summary>
<div class="doc-comment">
<p>Fetch list of known Gemini capsules from circumlunar.</p>
<p></p>
<p>Returns:</p>
<p>List of capsule URLs</p>
</div>
</details>
</li>
<li><code>extract_gemini_links</code> (gemini_transport.py)
<details><summary>Extract links from Gemini gemtext format.</summary>
<div class="doc-comment">
<p>Extract links from Gemini gemtext format.</p>
<p></p>
<p>Args:</p>
<p>gemtext: Gemini markup text</p>
<p></p>
<p>Returns:</p>
<p>List of (url, label) tuples</p>
</div>
</details>
</li>
<li><code>_get_cached</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Check SQLite cache. Return hash if exists and not expired.</span></li>
<li><code>_init_cache_db</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Initialize SQLite cache database.</span></li>
<li><code>_cache</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Store JARM hash in SQLite cache.</span></li>
<li><code>_get_gemini_ssl_context</code> (gemini_transport.py) — <span class="doc-comment-inline">Return cached TLS 1.3 SSL context for Gemini protocol.</span></li>
<li><code>_build_alpn_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build ALPN extension.</span></li>
<li><code>_build_supported_groups_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build supported groups extension.</span></li>
<li><code>_build_signature_algorithms_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build signature algorithms extension.</span></li>
<li><code>_build_supported_versions_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build supported versions extension.</span></li>
<li><code>_get_db</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Get or create database connection.</span></li>
<li><code>_build_ec_point_format_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build EC point formats extension.</span></li>
<li><code>close</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Close database connection.</span></li>
<li><code>_build_sni_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build SNI (Server Name Indication) extension.</span></li>
<li><code>_ensure_cache_dir</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Ensure cache directory exists.</span></li>
</ul>
</details>

<details><summary><strong>Class</strong> (3)</summary>
<ul>
<li><code>_JARMFingerprinter</code> (jarm_fingerprinter.py)
<details><summary>JARM TLS Fingerprinter.</summary>
<div class="doc-comment">
<p>JARM TLS Fingerprinter.</p>
<p></p>
<p>Computes JARM hash by sending 10 TLS Client Hello packets with different</p>
<p>configurations and hashing the responses.</p>
</div>
</details>
</li>
<li><code>GeminiResponse</code> (gemini_transport.py) — <span class="doc-comment-inline">Parsed Gemini response.</span></li>
<li><code>GeminiFinding</code> (gemini_transport.py) — <span class="doc-comment-inline">Represents parsed gemini content as a finding.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (16)</summary>
<ul>
<li><code>_build_client_hello</code> (jarm_fingerprinter.py)</li>
<li><code>_build_payloads</code> (jarm_fingerprinter.py)
<details><summary>Build the 10 TLS Client Hello payloads.</summary>
<div class="doc-comment">
<p>Build the 10 TLS Client Hello payloads.</p>
<p></p>
<p>Returns list of 10 raw TLS Client Hello bytes.</p>
</div>
</details>
</li>
<li><code>_extract_cipher_version</code> (jarm_fingerprinter.py)
<details><summary>Extract cipher suite and TLS version from TLS response.</summary>
<div class="doc-comment">
<p>Extract cipher suite and TLS version from TLS response.</p>
<p></p>
<p>Returns 6-character hex string (4 chars for cipher + 2 for version).</p>
</div>
</details>
</li>
<li><code>_compute_jarm_async</code> (jarm_fingerprinter.py)
<details><summary>Async JARM computation - uses asyncio.sleep instead of blocking time.sleep.</summary>
<div class="doc-comment">
<p>Async JARM computation - uses asyncio.sleep instead of blocking time.sleep.</p>
<p></p>
<p>Sends 10 TLS Client Hello packets and computes the JARM hash.</p>
</div>
</details>
</li>
<li><code>_send_tls_packet</code> (jarm_fingerprinter.py)</li>
<li><code>fingerprint</code> (jarm_fingerprinter.py)
<details><summary>Return JARM hash for domain, using cache if available.</summary>
<div class="doc-comment">
<p>Return JARM hash for domain, using cache if available.</p>
<p></p>
<p>Args:</p>
<p>domain: Target domain name</p>
<p>port: Target port (default 443)</p>
<p></p>
<p>Returns:</p>
<p>62-character JARM hash or None if unavailable</p>
</div>
</details>
</li>
<li><code>_get_cached</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Check SQLite cache. Return hash if exists and not expired.</span></li>
<li><code>_cache</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Store JARM hash in SQLite cache.</span></li>
<li><code>_build_alpn_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build ALPN extension.</span></li>
<li><code>_build_supported_groups_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build supported groups extension.</span></li>
<li><code>_build_signature_algorithms_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build signature algorithms extension.</span></li>
<li><code>_build_supported_versions_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build supported versions extension.</span></li>
<li><code>_get_db</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Get or create database connection.</span></li>
<li><code>_build_ec_point_format_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build EC point formats extension.</span></li>
<li><code>close</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Close database connection.</span></li>
<li><code>_build_sni_extension</code> (jarm_fingerprinter.py) — <span class="doc-comment-inline">Build SNI (Server Name Indication) extension.</span></li>
</ul>
</details>

<details><summary><strong>Constant</strong> (15)</summary>
<ul>
<li><code>GEMINI_BOOTSTRAP_HOSTS</code> (gemini_transport.py)</li>
<li><code>JARM_HASH_LENGTH</code> (jarm_fingerprinter.py)</li>
<li><code>CACHE_DB_PATH</code> (jarm_fingerprinter.py)</li>
<li><code>CACHE_TTL_DAYS</code> (jarm_fingerprinter.py)</li>
<li><code>CACHE_TTL_SECONDS</code> (jarm_fingerprinter.py)</li>
<li><code>PACKET_TIMEOUT</code> (jarm_fingerprinter.py)</li>
<li><code>TOTAL_TIMEOUT</code> (jarm_fingerprinter.py)</li>
<li><code>GEMINI_PORT</code> (gemini_transport.py)</li>
<li><code>GEMINI_DEFAULT_TIMEOUT</code> (gemini_transport.py)</li>
<li><code>GEMINI_MAX_RESPONSE_SIZE</code> (gemini_transport.py)</li>
<li><code>_GEMINI_SSL_CONTEXT</code> (gemini_transport.py)</li>
<li><code>KENNEDY_SEARCH_URL</code> (gemini_transport.py)</li>
<li><code>MAX_CRAWL_PAGES</code> (gemini_transport.py)</li>
<li><code>MAX_CRAWL_TIME</code> (gemini_transport.py)</li>
<li><code>GEMINI_LINK_PATTERN</code> (gemini_transport.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 1049 |
| Avg lines/file | 349 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

