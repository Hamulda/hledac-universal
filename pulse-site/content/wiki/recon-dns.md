+++
title = "recon/dns/"
weight = 10
description = "<think> The user wants me to create a module overview for the `recon/dns` module. Let me analyze the structural context:  1. **Module identity**: This is a DNS-related module in the `recon` namespace...."

[extra]
tier = 2
file_count = 3
total_lines = 1055
languages = "Python"
parent_path = "recon"
+++

<think>
The user wants me to create a module overview for the `recon/dns` module. Let me analyze the structural context:

1. **Module identity**: This is a DNS-related module in the `recon` namespace. It handles DNS operations including resolution and tunnel detection.

2. **Structure**: 3 Python files, 1055 total lines, ~351 lines/file average.

3. **Key components**:
 - **PassiveDNSResolver**: Multi-resolver DoH client with token-bucket rate limiting and TTL cache
 - **DNSTunnel Detector**: Cascade DNS tunneling detector with 4-layer system (entropy, n-gram, majority vote, LSTM+wavelet)
 - **PassiveDNSAdapter**: Adapter for sidecar runners
 - Supporting infrastructure like token bucket, resolver health tracking, caching

4. **Dependencies**: No outgoing or incoming dependencies detected - this is a leaf module with no dependents.

5. **Scale**: 47 functions, 12 classes, 45 methods, 12 constants - moderate complexity.

6. **Tier 2 module** - likely a foundational utility.

Now for the writing rules:
- First sentence: MUST state what the module DOES or its PURPOSE - infer from file names, symbol names, dependency position
- Focus on PURPOSE, RESPONSIBILITIES, ARCHITECTURAL ROLE
- Note if it's a hub/leaf/bridge
- 4-8 sentences
- Do NOT list files/classes unless central to identity
- Vary sentence structure
- Do NOT start with "The X module consists of..." or "This module contains..."

This module provides DNS resolution capabilities and DNS tunneling detection. It appears to be a self-contained utility module (no dependencies). It has two main functions:
1. Passive DNS resolution via DoH with multiple resolvers, rate limiting, caching
2. DNS tunnel detection using ML techniques

Since it has no dependencies (incoming or outgoing), it's a standalone utility - a leaf module that could be used by many things but doesn't rely on other codebase modules.


The `acquire` function stands out with 77 references, though this seems to be a broader concurrency utility shared across the codebase. I'll focus the overview on the module's dual purpose: enabling flexible DNS resolution through multiple DoH providers with intelligent caching and rate limiting, while simultaneously providing sophisticated tunnel detection through entropy analysis, n-gram patterns, and machine learning approaches.
</think>

The `recon/dns` module provides two distinct DNS capabilities: passive DNS resolution via DNS-over-HTTPS (DoH) with multi-resolver failover, and detection of DNS tunneling attacks using a cascaded machine learning pipeline. Its `PassiveDNSResolver` coordinates queries across six DoH providers (Cloudflare, Google, OpenDNS, Quad9, AdGuard, and NextDNS) with token-bucket rate limiting and TTL caching to balance throughput and reliability, while maintaining per-resolver health tracking with automatic circuit breaker recovery. The `DNSTunnelDetector` implements a four-layer detection system—entropy screening, n-gram linguistic analysis, majority voting, and an LSTM classifier with wavelet preprocessing—to identify encoded exfiltration and covert communication patterns in DNS queries. The module operates independently without external dependencies, positioning it as a self-contained utility that any reconnaissance component can invoke for DNS lookups or security analysis; however, its isolated nature means it carries no upstream blast radius risk, though modifications to its core resolver or detection logic would affect any caller. With 12 classes, 47 functions, and over 1000 lines spanning these two substantial concerns, the module concentrates significant complexity in a small footprint, blending infrastructure concerns (caching, rate limiting, health monitoring) with analytical ML components (wavelet transforms, LSTM inference).

## Structure

| Language | Files |
|---|---|
| Python | 3 |

### Largest Files

- `dns_tunnel_detector.py` (681 lines)
- `passive_dns.py` (373 lines)
- `__init__.py` (1 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>acquire</code> (Function) in passive_dns.py — referenced in 77 files</p>
<details><summary>Acquire a token, waiting if needed. Returns False on timeout.</summary></details>
<ul><li class="ref-list">Referenced by: __main__.py, _deduper.py, _hermes_cache.py, _slab.py, ane_embedder.py +70 more</li></ul>
</li>
<li>
<p><code>PassiveDNSAdapter</code> (Class) in passive_dns.py — referenced in 6 files</p>
<details><summary>Passive DNS adapter for use in sidecar runners.</summary>
<div class="doc-comment">
<p>Passive DNS adapter for use in sidecar runners.</p>
<p>Wraps PassiveDNSResolver, returns CanonicalFinding-compatible dicts.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, bgp_passive_dns_adapter.py, network_intelligence.py, sprint_scheduler_v1_archived.py</li></ul>
</li>
<li>
<p><code>PassiveDNSResolver</code> (Class) in passive_dns.py — referenced in 5 files</p>
<details><summary>Multi-resolver DoH client with token-bucket rate limiting and TTL cache.</summary>
<div class="doc-comment">
<p>Multi-resolver DoH client with token-bucket rate limiting and TTL cache.</p>
<p></p>
<p>Methods (all async):</p>
<p>- resolve(name, rdtype)       → list of str (A/AAAA/CNAME/TXT)</p>
<p>- resolve_https_rr(name)       → list of str (HTTPS RR values, RFC 9460)</p>
<p>- compare_resolvers(name, rdtype) → dict resolver→answers (censorship comparison)</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, exposure_correlator.py, network_intelligence.py</li></ul>
</li>
<li>
<p><code>DNSTunnelDetector</code> (Class) in dns_tunnel_detector.py — referenced in 3 files</p>
<details><summary>Cascade DNS tunneling detector.</summary>
<div class="doc-comment">
<p>Cascade DNS tunneling detector.</p>
<p></p>
<p>Implements a 4-layer cascaded detection system:</p>
<p>1. Fast entropy screening for quick filtering</p>
<p>2. N-gram analysis for linguistic patterns</p>
<p>3. Majority vote combination</p>
<p>4. Wavelet + LSTM for ambiguous cases</p>
<p></p>
<p>Example:</p>
<p>&gt;&gt;&gt; config = DNSTunnelConfig(entropy_threshold=4.2)</p>
<p>&gt;&gt;&gt; detector = DNSTunnelDetector(config)</p>
<p>&gt;&gt;&gt; await detector.initialize()</p>
<p>&gt;&gt;&gt; findings = await detector.analyze_queries(["example.com", "a1b2c3..."])</p>
<p>&gt;&gt;&gt; await detector.cleanup()</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py</li></ul>
</li>
<li>
<p><code>_TokenBucket</code> (Class) in passive_dns.py — referenced in 3 files</p>
<details><summary>Async token bucket with event-driven wait using asyncio.Condition.</summary>
<div class="doc-comment">
<p>Async token bucket with event-driven wait using asyncio.Condition.</p>
<p></p>
<p>ISSUE-018 fix: Replaced polling loop (await asyncio.sleep(0.05))</p>
<p>with event-driven wait using asyncio.Condition.notify_all().</p>
<p>This eliminates 40×/sec CPU spin during token wait.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: domain_rate_limiter.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (47)</summary>
<ul>
<li><code>analyze_pcap</code> (dns_tunnel_detector.py)
<details><summary>Stream-analyze a PCAP file for DNS tunneling.</summary>
<div class="doc-comment">
<p>Stream-analyze a PCAP file for DNS tunneling.</p>
<p></p>
<p>Processes PCAP files in streaming fashion to maintain constant</p>
<p>memory usage regardless of file size.</p>
<p></p>
<p>Args:</p>
<p>pcap_path: Path to PCAP file</p>
<p></p>
<p>Returns:</p>
<p>List of TunnelingFinding for suspicious/malicious queries</p>
</div>
</details>
</li>
<li><code>_do_query</code> (passive_dns.py) — <span class="doc-comment-inline">Query one resolver, return results or [] on error.</span></li>
<li><code>_ngram_analysis</code> (dns_tunnel_detector.py)
<details><summary>Analyze query using n-gram frequencies.</summary>
<div class="doc-comment">
<p>Analyze query using n-gram frequencies.</p>
<p></p>
<p>Compares bigram and trigram frequencies against English language</p>
<p>patterns to detect anomalous (likely encoded) strings.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string to analyze</p>
<p></p>
<p>Returns:</p>
<p>NGramScore with frequency and anomaly metrics</p>
</div>
</details>
</li>
<li><code>_majority_vote</code> (dns_tunnel_detector.py)
<details><summary>Combine detection layers using majority voting.</summary>
<div class="doc-comment">
<p>Combine detection layers using majority voting.</p>
<p></p>
<p>Args:</p>
<p>entropy_suspicious: Result from entropy screening</p>
<p>ngram_score: N-gram analysis results</p>
<p>encoding_patterns: Detected encoding patterns</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (verdict, confidence)</p>
</div>
</details>
</li>
<li><code>_analyze_single_query</code> (dns_tunnel_detector.py)
<details><summary>Analyze a single DNS query through all detection layers.</summary>
<div class="doc-comment">
<p>Analyze a single DNS query through all detection layers.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string</p>
<p></p>
<p>Returns:</p>
<p>TunnelingFinding with complete analysis</p>
</div>
</details>
</li>
<li><code>_detect_encoding_patterns</code> (dns_tunnel_detector.py)
<details><summary>Detect potential encoding patterns in query.</summary>
<div class="doc-comment">
<p>Detect potential encoding patterns in query.</p>
<p></p>
<p>Identifies Base32, Base64, and hexadecimal encoding patterns</p>
<p>commonly used in DNS tunneling.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string</p>
<p></p>
<p>Returns:</p>
<p>List of detected encoding types</p>
</div>
</details>
</li>
<li><code>_wavelet_preprocess</code> (dns_tunnel_detector.py)
<details><summary>Preprocess query using wavelet transform.</summary>
<div class="doc-comment">
<p>Preprocess query using wavelet transform.</p>
<p></p>
<p>Converts the query string into a 256-dimensional feature vector</p>
<p>using wavelet decomposition for LSTM input.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string</p>
<p></p>
<p>Returns:</p>
<p>256-dimensional numpy array</p>
</div>
</details>
</li>
<li><code>acquire</code> (passive_dns.py) — <span class="doc-comment-inline">Acquire a token, waiting if needed. Returns False on timeout.</span></li>
<li><code>get_healthy_resolvers</code> (passive_dns.py)
<details><summary>Return fallback chain with unhealthy resolvers filtered out.</summary>
<div class="doc-comment">
<p>Return fallback chain with unhealthy resolvers filtered out.</p>
<p></p>
<p>Recovery window: resolvers with consecutive_failures &gt; 0 but within</p>
<p>_RECOVERY_WINDOW_S are temporarily excluded so a transient DoH outage</p>
<p>doesn't permanently blacklist a provider. After recovery window expires</p>
<p>the resolver is re-added (failure count is reset to 0).</p>
</div>
</details>
</li>
<li><code>_fast_entropy_screen</code> (dns_tunnel_detector.py)
<details><summary>Fast entropy-based screening.</summary>
<div class="doc-comment">
<p>Fast entropy-based screening.</p>
<p></p>
<p>Quickly identifies high-entropy queries that may indicate tunneling.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string (domain name)</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (entropy_value, is_suspicious)</p>
<p>is_suspicious is None if inconclusive</p>
</div>
</details>
</li>
<li><code>_lstm_validate</code> (dns_tunnel_detector.py)
<details><summary>Validate query using LSTM classifier.</summary>
<div class="doc-comment">
<p>Validate query using LSTM classifier.</p>
<p></p>
<p>Runs the wavelet-preprocessed query through the LSTM model</p>
<p>to get a tunneling confidence score.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string</p>
<p></p>
<p>Returns:</p>
<p>Confidence score (0-1, higher = more likely tunneling)</p>
</div>
</details>
</li>
<li><code>analyze_queries</code> (dns_tunnel_detector.py)
<details><summary>Analyze a batch of DNS queries for tunneling.</summary>
<div class="doc-comment">
<p>Analyze a batch of DNS queries for tunneling.</p>
<p></p>
<p>Processes queries through the cascade detection system:</p>
<p>1. Fast entropy screening</p>
<p>2. N-gram analysis</p>
<p>3. Majority vote</p>
<p>4. LSTM validation for ambiguous cases</p>
<p></p>
<p>Args:</p>
<p>queries: List of DNS query strings to analyze</p>
<p></p>
<p>Returns:</p>
<p>List of TunnelingFinding with detection results</p>
</div>
</details>
</li>
<li><code>create_dns_tunnel_detector</code> (dns_tunnel_detector.py)
<details><summary>Factory function for creating DNS tunnel detector instances.</summary>
<div class="doc-comment">
<p>Factory function for creating DNS tunnel detector instances.</p>
<p></p>
<p>Creates a configured DNSTunnelDetector with graceful fallback</p>
<p>if dependencies are missing.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration. Uses defaults if None.</p>
<p></p>
<p>Returns:</p>
<p>Configured DNSTunnelDetector instance, or None if creation fails</p>
<p></p>
<p>Example:</p>
<p>&gt;&gt;&gt; detector = create_dns_tunnel_detector(DNSTunnelConfig(entropy_threshold=4.0))</p>
<p>&gt;&gt;&gt; if detector:</p>
<p>...     await detector.initialize()</p>
<p>...     findings = await detector.analyze_queries(["test.example.com"])</p>
</div>
</details>
</li>
<li><code>_calculate_entropy</code> (dns_tunnel_detector.py)
<details><summary>Calculate Shannon entropy of data.</summary>
<div class="doc-comment">
<p>Calculate Shannon entropy of data.</p>
<p></p>
<p>Args:</p>
<p>data: String or bytes to analyze</p>
<p></p>
<p>Returns:</p>
<p>Entropy in bits per character/byte</p>
</div>
</details>
</li>
<li><code>resolve</code> (passive_dns.py)
<details><summary>Resolve name via DoH fallback chain — F300.</summary>
<div class="doc-comment">
<p>Resolve name via DoH fallback chain — F300.</p>
<p></p>
<p>Tries resolvers in order (cloudflare → google → opendns → quad9 → adguard → nextdns).</p>
<p>Early exit on first successful resolution with results.</p>
<p>Records success/failure per resolver for circuit breaker health tracking.</p>
</div>
</details>
</li>
<li><code>__call__</code> (dns_tunnel_detector.py)
<details><summary>Forward pass through LSTM.</summary>
<div class="doc-comment">
<p>Forward pass through LSTM.</p>
<p></p>
<p>Args:</p>
<p>x: Input tensor of shape (batch, seq_len, features)</p>
<p></p>
<p>Returns:</p>
<p>Output logits of shape (batch, 1)</p>
</div>
</details>
</li>
<li><code>query</code> (passive_dns.py) — <span class="doc-comment-inline">Query passive DNS for a target (domain or IP).</span></li>
<li><code>initialize</code> (dns_tunnel_detector.py)
<details><summary>Initialize detector with bigram database and LSTM model.</summary>
<div class="doc-comment">
<p>Initialize detector with bigram database and LSTM model.</p>
<p></p>
<p>Loads the English bigram frequency database and initializes</p>
<p>the LSTM model if MLX is available and enabled.</p>
</div>
</details>
</li>
<li><code>_process_query_batch</code> (dns_tunnel_detector.py)
<details><summary>Process a batch of queries with their metadata.</summary>
<div class="doc-comment">
<p>Process a batch of queries with their metadata.</p>
<p></p>
<p>Args:</p>
<p>queries: List of query strings</p>
<p>metadata: List of (timestamp, src_ip, dst_ip) tuples</p>
<p></p>
<p>Returns:</p>
<p>List of findings (only suspicious/malicious unless all findings wanted)</p>
</div>
</details>
</li>
<li><code>compare_resolvers</code> (passive_dns.py)
<details><summary>Compare answers across all healthy resolvers — detects censorship.</summary>
<div class="doc-comment">
<p>Compare answers across all healthy resolvers — detects censorship.</p>
<p></p>
<p>F300: Uses health-aware resolver list. Unhealthy resolvers are excluded.</p>
</div>
</details>
</li>
<li><code>cleanup</code> (dns_tunnel_detector.py)
<details><summary>Clean up detector resources.</summary>
<div class="doc-comment">
<p>Clean up detector resources.</p>
<p></p>
<p>Releases memory used by the LSTM model and clears caches.</p>
</div>
</details>
</li>
<li><code>get_stats</code> (dns_tunnel_detector.py)
<details><summary>Get detection statistics.</summary>
<div class="doc-comment">
<p>Get detection statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with processing statistics</p>
</div>
</details>
</li>
<li><code>__init__</code> (dns_tunnel_detector.py)</li>
<li><code>__init__</code> (dns_tunnel_detector.py)
<details><summary>Initialize detector with configuration.</summary>
<div class="doc-comment">
<p>Initialize detector with configuration.</p>
<p></p>
<p>Args:</p>
<p>config: Detector configuration. Uses defaults if None.</p>
</div>
</details>
</li>
<li><code>get</code> (passive_dns.py)</li>
<li><code>set</code> (passive_dns.py)</li>
<li><code>record_success</code> (passive_dns.py)</li>
<li><code>record_failure</code> (passive_dns.py)</li>
<li><code>__init__</code> (passive_dns.py)</li>
<li><code>_get_lock</code> (passive_dns.py) — <span class="doc-comment-inline">Lazily create lock — ISSUE-014 fix: asyncio.Lock() at __init__ crashes on macOS.</span></li>
<li><code>_get_condition</code> (passive_dns.py) — <span class="doc-comment-inline">Lazily create Condition attached to the running event loop.</span></li>
<li><code>failure_rate</code> (passive_dns.py)</li>
<li><code>_ensure_session</code> (passive_dns.py)</li>
<li><code>__init__</code> (passive_dns.py)</li>
<li><code>get_stats</code> (passive_dns.py) — <span class="doc-comment-inline">Return per-resolver health stats for telemetry.</span></li>
<li><code>__init__</code> (passive_dns.py)</li>
<li><code>resolve_https_rr</code> (passive_dns.py) — <span class="doc-comment-inline">Query HTTPS RR (Type 65) via DoH.</span></li>
<li><code>close</code> (passive_dns.py)</li>
<li><code>is_healthy</code> (passive_dns.py)</li>
<li><code>_key</code> (passive_dns.py)</li>
<li><code>__init__</code> (passive_dns.py)</li>
<li><code>__init__</code> (passive_dns.py)</li>
<li><code>resolve</code> (passive_dns.py)</li>
<li><code>resolve_https_rr</code> (passive_dns.py)</li>
<li><code>compare_resolvers</code> (passive_dns.py)</li>
<li><code>close</code> (passive_dns.py)</li>
<li><code>_is_ipv6</code> (passive_dns.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (12)</summary>
<ul>
<li><code>DNSTunnelDetector</code> (dns_tunnel_detector.py)
<details><summary>Cascade DNS tunneling detector.</summary>
<div class="doc-comment">
<p>Cascade DNS tunneling detector.</p>
<p></p>
<p>Implements a 4-layer cascaded detection system:</p>
<p>1. Fast entropy screening for quick filtering</p>
<p>2. N-gram analysis for linguistic patterns</p>
<p>3. Majority vote combination</p>
<p>4. Wavelet + LSTM for ambiguous cases</p>
<p></p>
<p>Example:</p>
<p>&gt;&gt;&gt; config = DNSTunnelConfig(entropy_threshold=4.2)</p>
<p>&gt;&gt;&gt; detector = DNSTunnelDetector(config)</p>
<p>&gt;&gt;&gt; await detector.initialize()</p>
<p>&gt;&gt;&gt; findings = await detector.analyze_queries(["example.com", "a1b2c3..."])</p>
<p>&gt;&gt;&gt; await detector.cleanup()</p>
</div>
</details>
</li>
<li><code>PassiveDNSResolver</code> (passive_dns.py)
<details><summary>Multi-resolver DoH client with token-bucket rate limiting and TTL cache.</summary>
<div class="doc-comment">
<p>Multi-resolver DoH client with token-bucket rate limiting and TTL cache.</p>
<p></p>
<p>Methods (all async):</p>
<p>- resolve(name, rdtype)       → list of str (A/AAAA/CNAME/TXT)</p>
<p>- resolve_https_rr(name)       → list of str (HTTPS RR values, RFC 9460)</p>
<p>- compare_resolvers(name, rdtype) → dict resolver→answers (censorship comparison)</p>
</div>
</details>
</li>
<li><code>_ResolverHealthTracker</code> (passive_dns.py) — <span class="doc-comment-inline">Thread-safe resolver health tracker with recovery window.</span></li>
<li><code>_TokenBucket</code> (passive_dns.py)
<details><summary>Async token bucket with event-driven wait using asyncio.Condition.</summary>
<div class="doc-comment">
<p>Async token bucket with event-driven wait using asyncio.Condition.</p>
<p></p>
<p>ISSUE-018 fix: Replaced polling loop (await asyncio.sleep(0.05))</p>
<p>with event-driven wait using asyncio.Condition.notify_all().</p>
<p>This eliminates 40×/sec CPU spin during token wait.</p>
</div>
</details>
</li>
<li><code>PassiveDNSAdapter</code> (passive_dns.py)
<details><summary>Passive DNS adapter for use in sidecar runners.</summary>
<div class="doc-comment">
<p>Passive DNS adapter for use in sidecar runners.</p>
<p>Wraps PassiveDNSResolver, returns CanonicalFinding-compatible dicts.</p>
</div>
</details>
</li>
<li><code>LSTMTunnelClassifier</code> (dns_tunnel_detector.py)
<details><summary>MLX LSTM classifier for DNS tunneling detection.</summary>
<div class="doc-comment">
<p>MLX LSTM classifier for DNS tunneling detection.</p>
<p></p>
<p>2-layer LSTM with 128 hidden units for classifying DNS queries</p>
<p>as benign or malicious based on wavelet-transformed features.</p>
</div>
</details>
</li>
<li><code>_DoHCache</code> (passive_dns.py) — <span class="doc-comment-inline">TTL-cached DoH responses, bounded by MAX_DOH_CACHE_SIZE.</span></li>
<li><code>TunnelingFinding</code> (dns_tunnel_detector.py)
<details><summary>DNS tunneling detection finding.</summary>
<div class="doc-comment">
<p>DNS tunneling detection finding.</p>
<p></p>
<p>Attributes:</p>
<p>query: The DNS query string analyzed</p>
<p>entropy: Shannon entropy of the query (bits/character)</p>
<p>ngram_score: N-gram analysis results</p>
<p>lstm_score: LSTM confidence score (0-1)</p>
<p>verdict: Final detection verdict</p>
<p>confidence: Overall confidence in the verdict (0-1)</p>
<p>encoding_type: Detected encoding pattern (e.g., 'base64', 'base32', 'hex')</p>
<p>timestamp: Optional timestamp from PCAP</p>
<p>source_ip: Optional source IP address</p>
<p>dest_ip: Optional destination IP address</p>
</div>
</details>
</li>
<li><code>DNSTunnelConfig</code> (dns_tunnel_detector.py)
<details><summary>Configuration for DNS tunneling detector.</summary>
<div class="doc-comment">
<p>Configuration for DNS tunneling detector.</p>
<p></p>
<p>Attributes:</p>
<p>entropy_threshold: Shannon entropy threshold for fast screening (bits/char)</p>
<p>ngram_threshold: N-gram anomaly score threshold</p>
<p>lstm_threshold: LSTM confidence threshold for malicious classification</p>
<p>max_queries_per_batch: Maximum queries to process in a batch</p>
<p>enable_lstm: Whether to enable LSTM validation layer</p>
<p>pcap_chunk_seconds: Time window for PCAP streaming chunks</p>
<p>wavelet_levels: Number of wavelet decomposition levels</p>
<p>majority_vote_threshold: Minimum votes needed for definitive verdict</p>
</div>
</details>
</li>
<li><code>_ResolverHealth</code> (passive_dns.py) — <span class="doc-comment-inline">Per-resolver health state for circuit breaker.</span></li>
<li><code>NGramScore</code> (dns_tunnel_detector.py)
<details><summary>N-gram analysis score.</summary>
<div class="doc-comment">
<p>N-gram analysis score.</p>
<p></p>
<p>Attributes:</p>
<p>bigram_freq: Average bigram frequency score</p>
<p>trigram_freq: Average trigram frequency score</p>
<p>char_distribution: Character distribution entropy</p>
<p>anomaly_score: Combined anomaly score (0-1, higher = more anomalous)</p>
</div>
</details>
</li>
<li><code>Verdict</code> (dns_tunnel_detector.py) — <span class="doc-comment-inline">Detection verdict enumeration.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (45)</summary>
<ul>
<li><code>analyze_pcap</code> (dns_tunnel_detector.py)
<details><summary>Stream-analyze a PCAP file for DNS tunneling.</summary>
<div class="doc-comment">
<p>Stream-analyze a PCAP file for DNS tunneling.</p>
<p></p>
<p>Processes PCAP files in streaming fashion to maintain constant</p>
<p>memory usage regardless of file size.</p>
<p></p>
<p>Args:</p>
<p>pcap_path: Path to PCAP file</p>
<p></p>
<p>Returns:</p>
<p>List of TunnelingFinding for suspicious/malicious queries</p>
</div>
</details>
</li>
<li><code>_do_query</code> (passive_dns.py) — <span class="doc-comment-inline">Query one resolver, return results or [] on error.</span></li>
<li><code>_ngram_analysis</code> (dns_tunnel_detector.py)
<details><summary>Analyze query using n-gram frequencies.</summary>
<div class="doc-comment">
<p>Analyze query using n-gram frequencies.</p>
<p></p>
<p>Compares bigram and trigram frequencies against English language</p>
<p>patterns to detect anomalous (likely encoded) strings.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string to analyze</p>
<p></p>
<p>Returns:</p>
<p>NGramScore with frequency and anomaly metrics</p>
</div>
</details>
</li>
<li><code>_majority_vote</code> (dns_tunnel_detector.py)
<details><summary>Combine detection layers using majority voting.</summary>
<div class="doc-comment">
<p>Combine detection layers using majority voting.</p>
<p></p>
<p>Args:</p>
<p>entropy_suspicious: Result from entropy screening</p>
<p>ngram_score: N-gram analysis results</p>
<p>encoding_patterns: Detected encoding patterns</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (verdict, confidence)</p>
</div>
</details>
</li>
<li><code>_analyze_single_query</code> (dns_tunnel_detector.py)
<details><summary>Analyze a single DNS query through all detection layers.</summary>
<div class="doc-comment">
<p>Analyze a single DNS query through all detection layers.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string</p>
<p></p>
<p>Returns:</p>
<p>TunnelingFinding with complete analysis</p>
</div>
</details>
</li>
<li><code>_detect_encoding_patterns</code> (dns_tunnel_detector.py)
<details><summary>Detect potential encoding patterns in query.</summary>
<div class="doc-comment">
<p>Detect potential encoding patterns in query.</p>
<p></p>
<p>Identifies Base32, Base64, and hexadecimal encoding patterns</p>
<p>commonly used in DNS tunneling.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string</p>
<p></p>
<p>Returns:</p>
<p>List of detected encoding types</p>
</div>
</details>
</li>
<li><code>_wavelet_preprocess</code> (dns_tunnel_detector.py)
<details><summary>Preprocess query using wavelet transform.</summary>
<div class="doc-comment">
<p>Preprocess query using wavelet transform.</p>
<p></p>
<p>Converts the query string into a 256-dimensional feature vector</p>
<p>using wavelet decomposition for LSTM input.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string</p>
<p></p>
<p>Returns:</p>
<p>256-dimensional numpy array</p>
</div>
</details>
</li>
<li><code>acquire</code> (passive_dns.py) — <span class="doc-comment-inline">Acquire a token, waiting if needed. Returns False on timeout.</span></li>
<li><code>get_healthy_resolvers</code> (passive_dns.py)
<details><summary>Return fallback chain with unhealthy resolvers filtered out.</summary>
<div class="doc-comment">
<p>Return fallback chain with unhealthy resolvers filtered out.</p>
<p></p>
<p>Recovery window: resolvers with consecutive_failures &gt; 0 but within</p>
<p>_RECOVERY_WINDOW_S are temporarily excluded so a transient DoH outage</p>
<p>doesn't permanently blacklist a provider. After recovery window expires</p>
<p>the resolver is re-added (failure count is reset to 0).</p>
</div>
</details>
</li>
<li><code>_fast_entropy_screen</code> (dns_tunnel_detector.py)
<details><summary>Fast entropy-based screening.</summary>
<div class="doc-comment">
<p>Fast entropy-based screening.</p>
<p></p>
<p>Quickly identifies high-entropy queries that may indicate tunneling.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string (domain name)</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (entropy_value, is_suspicious)</p>
<p>is_suspicious is None if inconclusive</p>
</div>
</details>
</li>
<li><code>_lstm_validate</code> (dns_tunnel_detector.py)
<details><summary>Validate query using LSTM classifier.</summary>
<div class="doc-comment">
<p>Validate query using LSTM classifier.</p>
<p></p>
<p>Runs the wavelet-preprocessed query through the LSTM model</p>
<p>to get a tunneling confidence score.</p>
<p></p>
<p>Args:</p>
<p>query: DNS query string</p>
<p></p>
<p>Returns:</p>
<p>Confidence score (0-1, higher = more likely tunneling)</p>
</div>
</details>
</li>
<li><code>analyze_queries</code> (dns_tunnel_detector.py)
<details><summary>Analyze a batch of DNS queries for tunneling.</summary>
<div class="doc-comment">
<p>Analyze a batch of DNS queries for tunneling.</p>
<p></p>
<p>Processes queries through the cascade detection system:</p>
<p>1. Fast entropy screening</p>
<p>2. N-gram analysis</p>
<p>3. Majority vote</p>
<p>4. LSTM validation for ambiguous cases</p>
<p></p>
<p>Args:</p>
<p>queries: List of DNS query strings to analyze</p>
<p></p>
<p>Returns:</p>
<p>List of TunnelingFinding with detection results</p>
</div>
</details>
</li>
<li><code>_calculate_entropy</code> (dns_tunnel_detector.py)
<details><summary>Calculate Shannon entropy of data.</summary>
<div class="doc-comment">
<p>Calculate Shannon entropy of data.</p>
<p></p>
<p>Args:</p>
<p>data: String or bytes to analyze</p>
<p></p>
<p>Returns:</p>
<p>Entropy in bits per character/byte</p>
</div>
</details>
</li>
<li><code>resolve</code> (passive_dns.py)
<details><summary>Resolve name via DoH fallback chain — F300.</summary>
<div class="doc-comment">
<p>Resolve name via DoH fallback chain — F300.</p>
<p></p>
<p>Tries resolvers in order (cloudflare → google → opendns → quad9 → adguard → nextdns).</p>
<p>Early exit on first successful resolution with results.</p>
<p>Records success/failure per resolver for circuit breaker health tracking.</p>
</div>
</details>
</li>
<li><code>__call__</code> (dns_tunnel_detector.py)
<details><summary>Forward pass through LSTM.</summary>
<div class="doc-comment">
<p>Forward pass through LSTM.</p>
<p></p>
<p>Args:</p>
<p>x: Input tensor of shape (batch, seq_len, features)</p>
<p></p>
<p>Returns:</p>
<p>Output logits of shape (batch, 1)</p>
</div>
</details>
</li>
<li><code>query</code> (passive_dns.py) — <span class="doc-comment-inline">Query passive DNS for a target (domain or IP).</span></li>
<li><code>initialize</code> (dns_tunnel_detector.py)
<details><summary>Initialize detector with bigram database and LSTM model.</summary>
<div class="doc-comment">
<p>Initialize detector with bigram database and LSTM model.</p>
<p></p>
<p>Loads the English bigram frequency database and initializes</p>
<p>the LSTM model if MLX is available and enabled.</p>
</div>
</details>
</li>
<li><code>_process_query_batch</code> (dns_tunnel_detector.py)
<details><summary>Process a batch of queries with their metadata.</summary>
<div class="doc-comment">
<p>Process a batch of queries with their metadata.</p>
<p></p>
<p>Args:</p>
<p>queries: List of query strings</p>
<p>metadata: List of (timestamp, src_ip, dst_ip) tuples</p>
<p></p>
<p>Returns:</p>
<p>List of findings (only suspicious/malicious unless all findings wanted)</p>
</div>
</details>
</li>
<li><code>compare_resolvers</code> (passive_dns.py)
<details><summary>Compare answers across all healthy resolvers — detects censorship.</summary>
<div class="doc-comment">
<p>Compare answers across all healthy resolvers — detects censorship.</p>
<p></p>
<p>F300: Uses health-aware resolver list. Unhealthy resolvers are excluded.</p>
</div>
</details>
</li>
<li><code>cleanup</code> (dns_tunnel_detector.py)
<details><summary>Clean up detector resources.</summary>
<div class="doc-comment">
<p>Clean up detector resources.</p>
<p></p>
<p>Releases memory used by the LSTM model and clears caches.</p>
</div>
</details>
</li>
<li><code>get_stats</code> (dns_tunnel_detector.py)
<details><summary>Get detection statistics.</summary>
<div class="doc-comment">
<p>Get detection statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with processing statistics</p>
</div>
</details>
</li>
<li><code>__init__</code> (dns_tunnel_detector.py)</li>
<li><code>__init__</code> (dns_tunnel_detector.py)
<details><summary>Initialize detector with configuration.</summary>
<div class="doc-comment">
<p>Initialize detector with configuration.</p>
<p></p>
<p>Args:</p>
<p>config: Detector configuration. Uses defaults if None.</p>
</div>
</details>
</li>
<li><code>get</code> (passive_dns.py)</li>
<li><code>set</code> (passive_dns.py)</li>
<li><code>record_success</code> (passive_dns.py)</li>
<li><code>record_failure</code> (passive_dns.py)</li>
<li><code>__init__</code> (passive_dns.py)</li>
<li><code>_get_lock</code> (passive_dns.py) — <span class="doc-comment-inline">Lazily create lock — ISSUE-014 fix: asyncio.Lock() at __init__ crashes on macOS.</span></li>
<li><code>_get_condition</code> (passive_dns.py) — <span class="doc-comment-inline">Lazily create Condition attached to the running event loop.</span></li>
<li><code>failure_rate</code> (passive_dns.py)</li>
<li><code>_ensure_session</code> (passive_dns.py)</li>
<li><code>__init__</code> (passive_dns.py)</li>
<li><code>get_stats</code> (passive_dns.py) — <span class="doc-comment-inline">Return per-resolver health stats for telemetry.</span></li>
<li><code>__init__</code> (passive_dns.py)</li>
<li><code>resolve_https_rr</code> (passive_dns.py) — <span class="doc-comment-inline">Query HTTPS RR (Type 65) via DoH.</span></li>
<li><code>close</code> (passive_dns.py)</li>
<li><code>is_healthy</code> (passive_dns.py)</li>
<li><code>_key</code> (passive_dns.py)</li>
<li><code>__init__</code> (passive_dns.py)</li>
<li><code>__init__</code> (passive_dns.py)</li>
<li><code>resolve</code> (passive_dns.py)</li>
<li><code>resolve_https_rr</code> (passive_dns.py)</li>
<li><code>compare_resolvers</code> (passive_dns.py)</li>
<li><code>close</code> (passive_dns.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (12)</summary>
<ul>
<li><code>MAX_DOH_CACHE_SIZE</code> (passive_dns.py)</li>
<li><code>MAX_CENSORMAP_SIZE</code> (passive_dns.py)</li>
<li><code>DOH_CACHE_TTL_S</code> (passive_dns.py)</li>
<li><code>TOKEN_BUCKET_RATE</code> (passive_dns.py)</li>
<li><code>TOKEN_BUCKET_BURST</code> (passive_dns.py)</li>
<li><code>BGP_EVENT_TYPES</code> (passive_dns.py)</li>
<li><code>DOH_RESOLVERS</code> (passive_dns.py)</li>
<li><code>DOH_FALLBACK_CHAIN</code> (passive_dns.py)</li>
<li><code>_MAX_DOH_RETRIES</code> (passive_dns.py)</li>
<li><code>_DOH_RETRY_DELAY_S</code> (passive_dns.py)</li>
<li><code>_MAX_CONSECUTIVE_FAILURES</code> (passive_dns.py)</li>
<li><code>_RECOVERY_WINDOW_S</code> (passive_dns.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 1055 |
| Avg lines/file | 351 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

