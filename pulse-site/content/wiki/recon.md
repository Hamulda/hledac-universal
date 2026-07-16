+++
title = "recon/"
weight = 9
description = "<think> The user wants me to create a module overview for the 'recon' module based on the structural context provided. Let me analyze what this module does from the file names, symbol names, and overa..."

[extra]
tier = 1
file_count = 81
total_lines = 39621
languages = "Python"
has_mermaid = true
+++

<think>
The user wants me to create a module overview for the "recon" module based on the structural context provided. Let me analyze what this module does from the file names, symbol names, and overall structure.

Key observations:
1. This is a "recon" module - likely for reconnaissance/intelligence gathering
2. 81 Python files, ~40k lines of code
3. Has sub-modules: dns/, network/, protocols/, stealth/
4. Has many key classes like:
 - Relationship DiscoveryEngine - social network analysis, community detection
 - Identity StitchingEngine - cross-platform identity linking
 - Blockchain Forensics - blockchain analysis
 - Dark WebCrawler - Tor crawling
 - DNSTunnel Detector - DNS analysis
 - Document IntelligenceEngine - document analysis
 - Academic SearchEngine - academic paper search
 - Pattern MiningEngine - pattern detection
 - Workflow Orchestrator - orchestrating analysis workflows
 - Exposed ServiceHunter - finding exposed services
 - Temporal Archaeologist - historical content recovery
 - Network Reconnaissance - DNS enumeration, WHOIS, etc.

5. No incoming dependencies (Tier 1) but depends on 3 modules (knowledge, project_types, utils)
6. This is a leaf node in the dependency graph - no modules depend on it

This is a comprehensive OSINT (Open Source Intelligence) and reconnaissance toolkit. It collects intelligence from:
- DNS sources
- Network reconnaissance
- Web archives
- Academic sources
- Blockchain analysis
- Dark web
- Document analysis
- Open source collectors (paste sites, etc.)

The module has high fan-out (many different capabilities) but no incoming dependencies - it's a leaf module that provides intelligence gathering capabilities to the rest of the system.

Let me write the overview following the rules:
- First sentence: state what the module DOES
- Focus on purpose, responsibilities, architectural role


- It's a leaf-level intelligence collection engine without dependents
- No file/function enumeration unless it's truly central
- 4-8 sentences, varied structure
- No speculation, just facts from context
</think>

This module serves as the intelligence collection engine for the system, aggregating data from diverse external sources including DNS records, certificate transparency logs, web archives, academic databases, blockchain explorers, and dark web services. It functions as a leaf module with no upstream dependents, meaning it operates independently to gather raw intelligence that flows into higher-level analysis components elsewhere in the codebase.

The module implements multiple specialized engines for distinct intelligence domains: relationship and identity analysis (linking entities across platforms), document forensics (extracting metadata and detecting manipulation in files), pattern mining across temporal and behavioral data, and exposure correlation (discovering misconfigured services, open storage buckets, and subdomain takeover vulnerabilities). A workflow orchestrator coordinates multi-module analysis pipelines while maintaining bounded resource consumption on constrained environments like M1 8GB systems.

Architecturally, this represents the outermost data ingestion layer—a hub that consolidates reconnaissance from network reconnaissance, DNS enumeration, certificate transparency, blockchain forensics, and stealthy crawling operations. The stealth/ subdirectory particularly emphasizes evasion capabilities including DNS tunnel detection, Tor-based dark web crawling, and scraping mechanisms designed to minimize detection.

With 81 files totaling roughly 40,000 lines of Python code, the module is substantially larger than typical codebase modules, reflecting the breadth of external integrations required. Its four sub-modules (dns, network, protocols, stealth) partition concerns by protocol layer and operational mode, while test coverage in the tests/ directory and certificate handling in cert/ round out the structure.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_recon["<b>recon/</b>"]
    style m_recon fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_knowledge["knowledge/"]
    m_recon -->|2| m_knowledge
    m_project_types_py["project_types.py/"]
    m_recon -->|1| m_project_types_py
    m_utils["utils/"]
    m_recon -->|1| m_utils
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_recon "/wiki/recon/"
    click m_knowledge "/wiki/knowledge/"
    click m_project_types_py "/wiki/project_types.py/"
    click m_utils "/wiki/utils/"
{% end %}

## Structure

### Sub-modules

- [**dns/**](/wiki/recon-dns/) — 3 files, 1055 lines (Python)
- [**network/**](/wiki/recon-network/) — 3 files, 913 lines (Python)
- [**protocols/**](/wiki/recon-protocols/) — 3 files, 1049 lines (Python)
- [**stealth/**](/wiki/recon-stealth/) — 4 files, 1749 lines (Python)

| Language | Files |
|---|---|
| Python | 81 |

### Directories

| Directory | Files | Lines |
|---|---|---|
| stealth/ | 4 | 1749 |
| dns/ | 3 | 1055 |
| protocols/ | 3 | 1049 |
| network/ | 3 | 913 |
| tests/ | 2 | 289 |
| cert/ | 2 | 226 |

### Largest Files

- `relationship_discovery.py` (1827 lines)
- `document_intelligence.py` (1727 lines)
- `pattern_mining.py` (1251 lines)
- `identity_stitching.py` (1190 lines)
- `archive_discovery.py` (1140 lines)
- `workflow_orchestrator.py` (1080 lines)
- `exposed_service_hunter.py` (1055 lines)
- `blockchain_analyzer.py` (973 lines)
- `academic_search.py` (970 lines)
- `open_source_collectors.py` (961 lines)

<details><summary><strong>Show 71 more files</strong></summary>

- `network_reconnaissance.py` (956 lines)
- `web_intelligence.py` (948 lines)
- `temporal_archaeologist.py` (940 lines)
- `passive_fingerprint.py` (921 lines)
- `dark_web_intelligence.py` (865 lines)
- `exposure_clients.py` (847 lines)
- `cryptographic_intelligence.py` (819 lines)
- `exposure_correlator.py` (703 lines)
- `academic_discovery.py` (685 lines)
- `dns/dns_tunnel_detector.py` (681 lines)
- `stealth/scraper.py` (648 lines)
- `network/bgp_monitor.py` (637 lines)
- `whois_service.py` (637 lines)
- `input_detector.py` (636 lines)
- `stealth/monitor.py` (621 lines)
- `data_leak_hunter.py` (603 lines)
- `streaming_embedder.py` (596 lines)
- `lane.py` (590 lines)
- `protocols/jarm_fingerprinter.py` (570 lines)
- `temporal_analysis.py` (478 lines)
- `protocols/gemini_transport.py` (478 lines)
- `wayback_diff_miner.py` (438 lines)
- `advanced_image_osint.py` (431 lines)
- `leak_sentinel.py` (429 lines)
- `rir_correlator.py` (426 lines)
- `ct_log_client.py` (422 lines)
- `__init__.py` (411 lines)
- `stealth/_models.py` (404 lines)
- `social_identity_miner.py` (404 lines)
- `bgp_lane.py` (400 lines)
- `dns/passive_dns.py` (373 lines)
- `bgp_passive_dns_adapter.py` (372 lines)
- `attribution_scorer.py` (369 lines)
- `pastebin_monitor.py` (361 lines)
- `network_intelligence.py` (358 lines)
- `timeline_synthesizer.py` (339 lines)
- `dark_web_lane.py` (334 lines)
- `browser_pool.py` (326 lines)
- `entity_signal_extractor.py` (320 lines)
- `github_secret_scanner.py` (301 lines)
- `blockchain_analyzer_lane.py` (301 lines)
- `identity_stitching_canonical.py` (300 lines)
- `wayback_cdx.py` (296 lines)
- `greynoise_lane.py` (291 lines)
- `network/passive_fingerprint.py` (275 lines)
- `network_reconnaissance_lane.py` (272 lines)
- `kill_chain_tagger.py` (270 lines)
- `_graph_serde.py` (249 lines)
- `censys_lane.py` (231 lines)
- `shodan_lane.py` (229 lines)
- `cert/ct_log_scanner.py` (225 lines)
- `shodan_wrapper.py` (224 lines)
- `intel_seed.py` (223 lines)
- `pattern_mining_canonical.py` (216 lines)
- `commoncrawl_adapter.py` (215 lines)
- `doh_lane.py` (215 lines)
- `confidence_policy.py` (182 lines)
- `temporal_archaeologist_adapter.py` (179 lines)
- `ct_lane.py` (164 lines)
- `tests/probe_wayback_cdx.py` (151 lines)
- `onion_seed_manager.py` (143 lines)
- `tests/probe_bgp_lane.py` (138 lines)
- `bgp_advisor_adapter.py` (102 lines)
- `wayback_cdx_deep_adapter.py` (87 lines)
- `stealth_crawler.py` (81 lines)
- `stealth/__init__.py` (76 lines)
- `_http_helpers.py` (31 lines)
- `network/__init__.py` (1 lines)
- `cert/__init__.py` (1 lines)
- `dns/__init__.py` (1 lines)
- `protocols/__init__.py` (1 lines)

</details>


## Dependencies

Depends on **3 files** across **3 modules**.

**[knowledge/](@/wiki/knowledge.md)** (1 files):
- `duckdb_store.py`

**[project_types.py/](@/wiki/project_types.py.md)** (1 files):
- `project_types.py`

**[utils/](@/wiki/utils.md)** (1 files):
- `uma_budget.py`



## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>RelationshipDiscoveryEngine</code> (Class) in relationship_discovery.py — referenced in 10 files</p>
<details><summary>Advanced relationship discovery and social network analysis engine.</summary>
<div class="doc-comment">
<p>Advanced relationship discovery and social network analysis engine.</p>
<p></p>
<p>This engine provides comprehensive capabilities for discovering and analyzing</p>
<p>relationships between entities, including social network analysis, community</p>
<p>detection, hidden path finding, and influence propagation modeling.</p>
<p></p>
<p>M1 8GB Optimizations:</p>
<p>- Uses scipy.sparse for large graphs to minimize memory usage</p>
<p>- Streaming graph construction for incremental updates</p>
<p>- Memory-efficient algorithms with lazy evaluation</p>
<p>- MLX acceleration where beneficial for matrix operations</p>
<p></p>
<p>Example:</p>
<p>engine = RelationshipDiscoveryEngine()</p>
<p></p>
<p># Add entities</p>
<p>engine.add_entity(Entity("user1", "person", {"name": "Alice"}))</p>
<p>engine.add_entity(Entity("user2", "person", {"name": "Bob"}))</p>
<p></p>
<p># Add relationships</p>
<p>engine.add_relationship(Relationship("user1", "user2", "knows", strength=0.8))</p>
<p></p>
<p># Analyze</p>
<p>centrality = engine.calculate_centrality("betweenness")</p>
<p>communities = engine.detect_communities()</p>
<p>paths = engine.find_hidden_paths("user1", "user2", max_depth=3)</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, academic_discovery.py, deep.py, identity_stitching.py, prefetch_oracle.py +4 more</li></ul>
</li>
<li>
<p><code>AcademicSearchEngine</code> (Class) in academic_search.py — referenced in 8 files</p>
<details><summary>Main engine for Multi-Source Academic Search.</summary>
<div class="doc-comment">
<p>Main engine for Multi-Source Academic Search.</p>
<p></p>
<p>Coordinates query expansion, source selection, parallel execution,</p>
<p>and result deduplication.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, academic_discovery.py, acquisition_strategy.py, enhanced_research.py, executor.py +1 more</li></ul>
</li>
<li>
<p><code>BlockchainForensics</code> (Class) in blockchain_analyzer.py — referenced in 6 files</p>
<details><summary>Advanced blockchain forensics and analysis tool.</summary>
<div class="doc-comment">
<p>Advanced blockchain forensics and analysis tool.</p>
<p></p>
<p>M1 8GB Optimized:</p>
<p>- Async API calls with connection pooling</p>
<p>- LRU caching for API responses (5 min TTL)</p>
<p>- Streaming processing for large transaction histories</p>
<p>- Minimal memory footprint</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, acquisition_strategy.py, blockchain_analyzer_lane.py, test_f_a2_lazy_intelligence.py</li></ul>
</li>
<li>
<p><code>ArchiveResurrector</code> (Class) in archive_discovery.py — referenced in 5 files</p>
<details><summary>Advanced web archive content recovery system.</summary>
<div class="doc-comment">
<p>Advanced web archive content recovery system.</p>
<p></p>
<p>Features:</p>
<p>- Wayback Machine CDX API integration</p>
<p>- Search engine cache checking</p>
<p>- Social media archive access</p>
<p>- Content quality assessment</p>
<p>- Metadata extraction</p>
<p>- Concurrent processing</p>
<p></p>
<p>Integrated from stealth_osint for universal orchestrator.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, enhanced_research.py, security_coordinator.py, test_html_parser_characterization.py</li></ul>
</li>
<li>
<p><code>DarkWebCrawler</code> (Class) in dark_web_intelligence.py — referenced in 4 files</p>
<details><summary>Advanced dark web crawler for OSINT research.</summary>
<div class="doc-comment">
<p>Advanced dark web crawler for OSINT research.</p>
<p></p>
<p>Crawls Tor hidden services and extracts intelligence:</p>
<p>- Hidden service enumeration</p>
<p>- Content extraction and indexing</p>
<p>- Cryptocurrency address harvesting</p>
<p>- PGP key discovery</p>
<p>- Link graph analysis</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: dark_web_lane.py, sprint_scheduler_v1_archived.py, test_issue17_bfs_crawl.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (989)</summary>
<ul>
<li><code>infer_tech_from_jobs</code> (web_intelligence.py)
<details><summary>Infer technology stack from job postings across multiple sources.</summary>
<div class="doc-comment">
<p>Infer technology stack from job postings across multiple sources.</p>
<p></p>
<p>Sources:</p>
<p>- Indeed RSS: https://www.indeed.com/rss?q={entity_name}+engineer</p>
<p>- Hacker News "Who is Hiring": HN API topstories.json filtered monthly</p>
<p>- Remoteok.com API: https://remoteok.io/api?tag={entity_name}</p>
<p></p>
<p>Args:</p>
<p>entity_name: Company/entity name to search job postings for</p>
<p></p>
<p>Returns:</p>
<p>TechIntelligence with detected_technologies, hiring_patterns,</p>
<p>seniority_distribution, and inferred_pain_points</p>
</div>
</details>
</li>
<li><code>_extract_tech_stack</code> (passive_fingerprint.py)
<details><summary>R11: Extract tech stack signals from HTTP response data.</summary>
<div class="doc-comment">
<p>R11: Extract tech stack signals from HTTP response data.</p>
<p></p>
<p>Detects:</p>
<p>- Cloud providers: AWS (x-amz-*), GCP (x-goog-*), Azure (x-ms-*),</p>
<p>Cloudflare (cf-ray), Fastly, Akamai</p>
<p>- WAF: Cloudflare WAF (403 + 1020), AWS WAF, Imperva (incap_ses),</p>
<p>Akamai, F5 BIG-IP</p>
<p>- CMS: WordPress, Drupal, Joomla, Typo3 (with version from readme/changelog)</p>
<p></p>
<p>Uses ahocorasick for O(n) multi-pattern matching when available,</p>
<p>falls back to regex for single patterns.</p>
<p></p>
<p>Args:</p>
<p>headers: HTTP response headers (lowercase keys)</p>
<p>html_head: HTML &lt;head&gt; content (truncated)</p>
<p>cookies: list of cookie strings</p>
<p></p>
<p>Returns:</p>
<p>TechStack with detected signals and confidence scores.</p>
</div>
</details>
</li>
<li><code>search_paste_sites</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search paste sites for secrets/leaks.</span></li>
<li><code>extract_and_encode_images</code> (dark_web_intelligence.py)
<details><summary>Sprint F214R: Extract images from crawled HTML and store VisionEncoder embeddings.</summary>
<div class="doc-comment">
<p>Sprint F214R: Extract images from crawled HTML and store VisionEncoder embeddings.</p>
<p></p>
<p>Gate: HLEDAC_ENABLE_IMAGE_OSINT=1 (default: off).</p>
<p>Bounded: max 3 images per page, 512KB per image, 8s timeout.</p>
<p>Fail-soft: any exception → log warning, return [].</p>
</div>
</details>
</li>
<li><code>correlate_findings</code> (workflow_orchestrator.py)
<details><summary>Correlate findings and produce grouped themes with risk scoring.</summary>
<div class="doc-comment">
<p>Correlate findings and produce grouped themes with risk scoring.</p>
<p></p>
<p>Pure function - no side effects, no storage, no orchestrator dependency.</p>
<p>Works with finding-like dicts, IOC dicts, or any dict with:</p>
<p>- type / finding_type / indicator_type</p>
<p>- severity (critical/high/medium/low)</p>
<p>- confidence (0.0-1.0)</p>
<p>- description / description_text</p>
<p>- source / module / tag / tags</p>
<p></p>
<p>Args:</p>
<p>findings: List of finding dictionaries</p>
<p>risk_thresholds: Optional custom risk thresholds</p>
<p>max_themes: Maximum number of themes to return (default 10)</p>
<p></p>
<p>Returns:</p>
<p>CorrelationResult with themes, risk_score, buckets, top_themes</p>
<p></p>
<p>Example:</p>
<p>findings = [</p>
<p>{"type": "ioc", "severity": "high", "confidence": 0.9,</p>
<p>"description": "Malicious domain found", "source": "dns"},</p>
<p>{"type": "pattern", "severity": "medium", "confidence": 0.7,</p>
<p>"description": "Suspicious encoding", "source": "encoding"},</p>
<p>]</p>
<p>result = correlate_findings(findings)</p>
<p># result.themes, result.risk_score, result.risk_buckets, result.top_themes</p>
</div>
</details>
</li>
<li><code>detect_communities</code> (relationship_discovery.py)
<details><summary>Detect communities in the relationship graph.</summary>
<div class="doc-comment">
<p>Detect communities in the relationship graph.</p>
<p></p>
<p>Args:</p>
<p>algorithm: Community detection algorithm (louvain, label_propagation)</p>
<p>resolution: Resolution parameter for Louvain algorithm</p>
<p></p>
<p>Returns:</p>
<p>List of detected communities</p>
</div>
</details>
</li>
<li><code>search_academic</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search academic preprint servers.</span></li>
<li><code>model_influence_propagation</code> (relationship_discovery.py)
<details><summary>Model influence propagation through the network.</summary>
<div class="doc-comment">
<p>Model influence propagation through the network.</p>
<p></p>
<p>Args:</p>
<p>seed_entities: Initial influential entities</p>
<p>iterations: Maximum iterations</p>
<p>damping: Damping factor for propagation</p>
<p>convergence_threshold: Convergence threshold</p>
<p></p>
<p>Returns:</p>
<p>InfluenceModel with propagation results</p>
</div>
</details>
</li>
<li><code>find_hidden_paths</code> (relationship_discovery.py)
<details><summary>Find hidden connection paths between two entities.</summary>
<div class="doc-comment">
<p>Find hidden connection paths between two entities.</p>
<p></p>
<p>Args:</p>
<p>entity_a: Starting entity ID</p>
<p>entity_b: Target entity ID</p>
<p>max_depth: Maximum path length</p>
<p>min_strength: Minimum relationship strength threshold</p>
<p>max_paths: Maximum number of paths to return</p>
<p></p>
<p>Returns:</p>
<p>List of connection paths</p>
</div>
</details>
</li>
<li><code>_deduplicate_and_rank</code> (academic_search.py)
<details><summary>Unified deduplication + ranking via single TaskGroup + Queue pipeline.</summary>
<div class="doc-comment">
<p>Unified deduplication + ranking via single TaskGroup + Queue pipeline.</p>
<p></p>
<p>Pass 1 (producer): builds DedupItems from SearchResults (CPU-bound hash).</p>
<p>Queue (maxsize=512): backpressure when consumer is slower than producer.</p>
<p>Pass 2 (consumer): deduplicates then ranks items pulled from queue.</p>
<p></p>
<p>Both passes run concurrently within a single TaskGroup — no GIL</p>
<p>serialization between them. CPU-bound work runs on asyncio.to_thread</p>
<p>which releases the GIL during the hash/scoring computation.</p>
</div>
</details>
</li>
<li><code>compute_match</code> (identity_stitching.py)
<details><summary>Compute match between two profiles.</summary>
<div class="doc-comment">
<p>Compute match between two profiles.</p>
<p></p>
<p>Args:</p>
<p>profile_a: First profile</p>
<p>profile_b: Second profile</p>
<p></p>
<p>Returns:</p>
<p>IdentityMatch with scores and signals</p>
</div>
</details>
</li>
<li><code>_correlate_signals</code> (exposure_correlator.py)
<details><summary>Correlate signals into exposure findings.</summary>
<div class="doc-comment">
<p>Correlate signals into exposure findings.</p>
<p></p>
<p>Algorithm:</p>
<p>1. Group signals by asset_key (bounded to MAX_ASSETS)</p>
<p>2. For each asset with multiple signal types, attempt correlation</p>
<p>3. For JARM fingerprints, cluster assets by hash (infra_cluster)</p>
<p>4. For each successful correlation, produce an ExposureFinding</p>
<p></p>
<p>Bounded:</p>
<p>- MAX_ASSETS=1000: skip assets beyond this cap</p>
<p>- MAX_SIGNALS_PER_ASSET=3: only keep first 3 signals per asset</p>
<p>- MAX_FINDINGS=500: cap total findings produced</p>
</div>
</details>
</li>
<li><code>traverse_citation_graph</code> (academic_discovery.py)</li>
<li><code>find_all_matches_async</code> (identity_stitching.py)
<details><summary>Find all matches across all profiles — MUST be called from async context.</summary>
<div class="doc-comment">
<p>Find all matches across all profiles — MUST be called from async context.</p>
<p></p>
<p>O(N²) brute-force replaced by:</p>
<p>- LSH pre-filtering: O(1) candidate reduction per profile</p>
<p>- Parallel async pairwise: bounded semaphore, concurrency=10</p>
<p>Falls back to O(N²) when LSH unavailable.</p>
</div>
</details>
</li>
<li><code>search_censys</code> (exposed_service_hunter.py)
<details><summary>Search Censys using free API (Censys data API).</summary>
<div class="doc-comment">
<p>Search Censys using free API (Censys data API).</p>
<p></p>
<p>Args:</p>
<p>query: Search query (e.g., "services.tls.certificates.leaf_data.subject.common_name: example.com")</p>
<p>api_id: Censys API ID (default: CENSYS_API_ID env var)</p>
<p>api_secret: Censys API Secret (default: CENSYS_API_SECRET env var)</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with structure:</p>
<p>[{'ip': str, 'port': int, 'service': str, 'banner': str}]</p>
<p></p>
<p>Anti-patterns:</p>
<p>- Rate limited (uses APICache with 1-hour TTL)</p>
<p>- No API credentials hardcoded (uses .env)</p>
</div>
</details>
</li>
<li><code>_scrape_paste_site</code> (open_source_collectors.py)
<details><summary>Canonical base for paste-site scrapers.</summary>
<div class="doc-comment">
<p>Canonical base for paste-site scrapers.</p>
<p></p>
<p>Pipeline (M1 8GB friendly):</p>
<p>1. LRU+TTL cache check → return on hit (bounded by _PASTE_CACHE_MAX)</p>
<p>2. Atomic claim leadership via dict.setdefault (race-free under</p>
<p>concurrent gather — N callers → exactly 1 leader, N-1 followers)</p>
<p>3. Run fetch under per-host semaphore (M1 soft cap)</p>
<p>4. Build URL(s) via adapter → fetch with timeout/byte cap</p>
<p>5. adapter.parse() → return parsed text; None falls through to next URL</p>
<p>6. Cache write (positive and negative results)</p>
<p>7. Fail-soft: any Exception → None; CancelledError → re-raise (invariant)</p>
<p></p>
<p>Concurrent callers for the same (site, paste_id) await the same Future</p>
<p>and receive the same text-or-None result. No duplicate network requests.</p>
</div>
</details>
</li>
<li><code>extract_fingerprints</code> (passive_fingerprint.py)
<details><summary>Extract all fingerprints from a single CanonicalFinding.</summary>
<div class="doc-comment">
<p>Extract all fingerprints from a single CanonicalFinding.</p>
<p></p>
<p>Checks HTTP headers, TLS/cert data, CT metadata, and HTML content.</p>
<p>Returns up to MAX_FINGERPRINTS_PER_FINDING fingerprints.</p>
<p></p>
<p>Bounds:</p>
<p>- MAX_FINGERPRINTS_PER_FINDING = 5</p>
<p>- MAX_PATTERN_BYTES = 4096</p>
</div>
</details>
</li>
<li><code>search_usenet</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search Usenet archives via Google Groups and GMane.</span></li>
<li><code>_extract_metadata_html</code> (archive_discovery.py)
<details><summary>Extract metadata from HTML content.</summary>
<div class="doc-comment">
<p>Extract metadata from HTML content.</p>
<p></p>
<p>Tier 2 migration: selectolax-first → bs4 fallback → regex/stdlib fallback.</p>
</div>
</details>
</li>
<li><code>_extract_tech_stack_findings</code> (passive_fingerprint.py)
<details><summary>R11: Extract tech-stack signals from existing public findings.</summary>
<div class="doc-comment">
<p>R11: Extract tech-stack signals from existing public findings.</p>
<p>No live network, no deep_probe, no MLX.</p>
</div>
</details>
</li>
<li><code>stitch_identities</code> (identity_stitching.py)
<details><summary>Stitch identities based on matches.</summary>
<div class="doc-comment">
<p>Stitch identities based on matches.</p>
<p></p>
<p>O(α(N)) Union-Find clustering nahrazuje O(N²) connected_components.</p>
<p>Zároveň opraven bug: profile_ids → comp_profile_ids na řádku StitchedIdentity.</p>
<p></p>
<p>Args:</p>
<p>match_threshold: Threshold for direct stitching</p>
<p>transitive_threshold: Threshold for transitive stitching (unused, kept for compat)</p>
<p></p>
<p>Returns:</p>
<p>List of StitchedIdentity objects</p>
</div>
</details>
</li>
<li><code>recover_deleted_content</code> (temporal_archaeologist.py)
<details><summary>Recover deleted content from multiple archive sources.</summary>
<div class="doc-comment">
<p>Recover deleted content from multiple archive sources.</p>
<p></p>
<p>Args:</p>
<p>url: URL to recover</p>
<p>sources: List of sources to check (default: all)</p>
<p>from_date: Start date for recovery</p>
<p>to_date: End date for recovery</p>
<p>include_content: Whether to fetch full content</p>
<p></p>
<p>Returns:</p>
<p>RecoveryResult with recovered versions</p>
</div>
</details>
</li>
<li><code>_recover_from_wayback</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Wayback Machine.</span></li>
<li><code>_load_graph</code> (relationship_discovery.py)
<details><summary>Load a graph from disk.</summary>
<div class="doc-comment">
<p>Load a graph from disk.</p>
<p></p>
<p>Format policy (F-BLOOM-REGRESSION companion):</p>
<p>* Our JSON envelope (orjson + node_link) is the canonical read path.</p>
<p>No ``pickle.load`` exec surface.</p>
<p>* igraph native ``Graph.Load`` (NOT Python pickle) is used for files</p>
<p>that don't match our JSON magic.</p>
<p>* Legacy ``.pkl`` (Python pickle) is only accepted as a one-shot</p>
<p>migration and ONLY on F196B-safe paths (``~/.hledac/graphs``).</p>
<p></p>
<p>SECURITY: F196B — legacy pickle is rejected outside the application's</p>
<p>graph directory. New code never writes Python pickle.</p>
</div>
</details>
</li>
<li><code>search_matrix</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search public Matrix rooms.</span></li>
<li><code>extract_signals</code> (exposure_correlator.py)
<details><summary>Extract asset signals from a list of CanonicalFindings.</summary>
<div class="doc-comment">
<p>Extract asset signals from a list of CanonicalFindings.</p>
<p></p>
<p>Signal types extracted:</p>
<p>- ct_cert: from ct_log findings (san = finding_id)</p>
<p>- open_bucket: from open_storage findings</p>
<p>- jarm_fp: from jarm fingerprint findings</p>
<p>- passive_dns: from passive_dns findings</p>
<p></p>
<p>Returns:</p>
<p>List of AssetSignal objects (unbounded within a sprint, but bounded</p>
<p>per-call via MAX_SIGNALS_PER_ASSET during correlation).</p>
</div>
</details>
</li>
<li><code>hunt</code> (exposed_service_hunter.py)
<details><summary>Perform comprehensive exposed service hunt.</summary>
<div class="doc-comment">
<p>Perform comprehensive exposed service hunt.</p>
<p></p>
<p>Args:</p>
<p>target: Target domain or company name</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with categorized findings</p>
</div>
</details>
</li>
<li><code>search_shodan</code> (exposed_service_hunter.py)
<details><summary>Search Shodan using free API (no key or community key).</summary>
<div class="doc-comment">
<p>Search Shodan using free API (no key or community key).</p>
<p></p>
<p>Args:</p>
<p>query: Search query (e.g., "apache", "nginx", "product:cisco")</p>
<p>api_key: Shodan API key (default: SHODAN_API_KEY env var)</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with structure:</p>
<p>[{'ip': str, 'port': int, 'service': str, 'banner': str}]</p>
<p></p>
<p>Anti-patterns:</p>
<p>- Rate limited (uses APICache with 1-hour TTL)</p>
<p>- No API key hardcoded (uses .env)</p>
</div>
</details>
</li>
<li><code>_fetch_osv_batch</code> (exposure_clients.py)
<details><summary>Fetch CVEs via OSV.dev batch API.</summary>
<div class="doc-comment">
<p>Fetch CVEs via OSV.dev batch API.</p>
<p>Yields dicts with CVE data. Falls back to NVD on 0 results.</p>
</div>
</details>
</li>
<li><code>intelligence_crosslink</code> (academic_discovery.py)
<details><summary>Extract emails from author strings, check breach APIs, extract institutions,</summary>
<div class="doc-comment">
<p>Extract emails from author strings, check breach APIs, extract institutions,</p>
<p>and find relationships via RelationshipDiscoveryEngine.</p>
</div>
</details>
</li>
<li><code>search_academic_all</code> (academic_discovery.py)</li>
<li><code>extract_tls_signals</code> (passive_fingerprint.py)
<details><summary>Extract TLS/certificate signals from finding payload_text.</summary>
<div class="doc-comment">
<p>Extract TLS/certificate signals from finding payload_text.</p>
<p></p>
<p>Returns dict with keys:</p>
<p>- cert_subject: certificate subject CN</p>
<p>- cert_issuer: certificate issuer</p>
<p>- cert_san: subject alternative names</p>
<p>- cipher_suite: negotiated cipher suite</p>
<p>- protocol_version: TLS version</p>
<p>- all_text: combined cert text for pattern matching</p>
</div>
</details>
</li>
<li><code>correlate_passive_fingerprints</code> (passive_fingerprint.py)
<details><summary>F204G: Extract passive service fingerprints from sprint findings.</summary>
<div class="doc-comment">
<p>F204G: Extract passive service fingerprints from sprint findings.</p>
<p></p>
<p>Entry point for the passive fingerprinting sidecar.</p>
<p></p>
<p>Pipeline:</p>
<p>1. Iterate over findings (bounded to MAX_FINGERPRINT_FINDINGS)</p>
<p>2. Extract signals from payload_text (HTTP/TLS/CT/HTML)</p>
<p>3. Match patterns to identify services</p>
<p>4. Convert to CanonicalFinding list</p>
<p>5. Return for async_ingest_findings_batch ingestion</p>
<p></p>
<p>Bounds enforced:</p>
<p>- MAX_FINGERPRINT_FINDINGS = 1000</p>
<p>- MAX_FINGERPRINTS_PER_FINDING = 5</p>
<p>- MAX_PATTERN_BYTES = 4096</p>
<p></p>
<p>Fail-soft: returns [] on any error.</p>
<p></p>
<p>Returns:</p>
<p>List of CanonicalFinding with source_type="passive_fingerprint".</p>
</div>
</details>
</li>
<li><code>forecast_mamba2</code> (pattern_mining.py)
<details><summary>Forecast using Mamba2 model with best-effort timeout and circuit breaker.</summary>
<div class="doc-comment">
<p>Forecast using Mamba2 model with best-effort timeout and circuit breaker.</p>
<p></p>
<p>Args:</p>
<p>series: Time series data</p>
<p>horizon: Number of steps to forecast</p>
<p></p>
<p>Returns:</p>
<p>List of forecasted values or None on failure</p>
</div>
</details>
</li>
<li><code>analyze_transaction_flows</code> (pattern_mining.py)
<details><summary>Analyze transaction flows for patterns.</summary>
<div class="doc-comment">
<p>Analyze transaction flows for patterns.</p>
<p></p>
<p>Args:</p>
<p>transactions: List of financial transactions</p>
<p>min_transactions: Minimum transactions required</p>
<p></p>
<p>Returns:</p>
<p>FlowPattern with transaction flow analysis</p>
</div>
</details>
</li>
<li><code>detect_wildcard</code> (network_reconnaissance.py)
<details><summary>Detect wildcard DNS configuration for a domain.</summary>
<div class="doc-comment">
<p>Detect wildcard DNS configuration for a domain.</p>
<p></p>
<p>Uses high-entropy random subdomains to probe for wildcard responses.</p>
<p>Conservative approach: returns wildcard_suspected=False on errors/ambiguity.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to check for wildcard DNS</p>
<p></p>
<p>Returns:</p>
<p>Dict with:</p>
<p>- wildcard_suspected: bool</p>
<p>- probe_count: int</p>
<p>- responses: list of probe results</p>
<p>- probe_method: str</p>
</div>
</details>
</li>
<li><code>calculate_centrality</code> (relationship_discovery.py)
<details><summary>Calculate centrality metrics for all entities.</summary>
<div class="doc-comment">
<p>Calculate centrality metrics for all entities.</p>
<p></p>
<p>Args:</p>
<p>metric: Centrality metric (betweenness, closeness, degree, eigenvector, pagerank)</p>
<p>use_mlx: Use MLX acceleration if available</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping entity IDs to centrality scores</p>
</div>
</details>
</li>
<li><code>gather_all</code> (open_source_collectors.py)
<details><summary>Gather from all or specified sources.</summary>
<div class="doc-comment">
<p>Gather from all or specified sources.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>sources: List of sources to search. If None, searches all.</p>
<p>Options: pastebin, usenet, matrix, academic, sec_edgar, court_records</p>
<p></p>
<p>Returns:</p>
<p>Dict mapping source name to list of finding dicts</p>
</div>
</details>
</li>
<li><code>load_graph</code> (relationship_discovery.py)
<details><summary>Load persisted NetworkX graph from disk with node-count bound.</summary>
<div class="doc-comment">
<p>Load persisted NetworkX graph from disk with node-count bound.</p>
<p></p>
<p>Reads JSON envelope (orjson + node_link). Legacy ``.pkl`` is accepted</p>
<p>only on F196B-safe paths as a one-shot migration.</p>
<p></p>
<p>Returns True if loaded, False if file missing or error.</p>
</div>
</details>
</li>
<li><code>analyze_massive_dump</code> (document_intelligence.py)
<details><summary>Analyze massive text dump using MLX acceleration.</summary>
<div class="doc-comment">
<p>Analyze massive text dump using MLX acceleration.</p>
<p></p>
<p>Args:</p>
<p>text: Large text to analyze (can be millions of tokens)</p>
<p>source: Source identifier</p>
<p>extract_entities: Whether to extract entities</p>
<p>build_timeline: Whether to build timeline</p>
<p>cross_reference: Whether to cross-reference entities</p>
<p></p>
<p>Returns:</p>
<p>LongContextAnalysis with all findings</p>
</div>
</details>
</li>
<li><code>execute_intelligence_operation</code> (web_intelligence.py)
<details><summary>Execute comprehensive intelligence operation on target.</summary>
<div class="doc-comment">
<p>Execute comprehensive intelligence operation on target.</p>
<p></p>
<p>Args:</p>
<p>target: Intelligence target configuration</p>
<p>operation_types: Types of operations to perform (default: all available)</p>
<p></p>
<p>Returns:</p>
<p>Operation ID for tracking results</p>
</div>
</details>
</li>
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
<li><code>analyze_image</code> (document_intelligence.py)
<details><summary>Analyze image for forensic artifacts.</summary>
<div class="doc-comment">
<p>Analyze image for forensic artifacts.</p>
<p></p>
<p>Uses ProcessPoolExecutor for CPU-bound image analysis (ELA) to avoid</p>
<p>contention with MLX workers. M1 8GB safe: max 2 workers.</p>
<p></p>
<p>Args:</p>
<p>content: Image bytes</p>
<p>url: Optional URL of the image for graph integration (S49-C)</p>
<p></p>
<p>Returns:</p>
<p>Dict with analysis results including ela_score, suspicious flag, etc.</p>
</div>
</details>
</li>
<li><code>_execute_module</code> (workflow_orchestrator.py)
<details><summary>Execute a single module.</summary>
<div class="doc-comment">
<p>Execute a single module.</p>
<p></p>
<p>Args:</p>
<p>module: Module name</p>
<p>input_data: Input data</p>
<p>context: Shared execution context</p>
<p></p>
<p>Returns:</p>
<p>Module execution result</p>
</div>
</details>
</li>
<li><code>search_arxiv</code> (academic_discovery.py)
<details><summary>Search arXiv for academic papers.</summary>
<div class="doc-comment">
<p>Search arXiv for academic papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>max_results: Maximum results to return (default 10)</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with keys: title, authors, year, link</p>
</div>
</details>
</li>
<li><code>search_crossref</code> (academic_discovery.py)
<details><summary>Search Crossref for academic papers.</summary>
<div class="doc-comment">
<p>Search Crossref for academic papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>max_results: Maximum results to return (default 10)</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with keys: title, authors, year, link</p>
</div>
</details>
</li>
<li><code>extract_html_signals</code> (passive_fingerprint.py)
<details><summary>Extract HTML content signals for service fingerprinting.</summary>
<div class="doc-comment">
<p>Extract HTML content signals for service fingerprinting.</p>
<p></p>
<p>Returns dict with keys:</p>
<p>- title: page title</p>
<p>- generator: meta generator tag</p>
<p>- scripts: script src patterns</p>
<p>- all_text: combined HTML text</p>
</div>
</details>
</li>
<li><code>_probe_pdf</code> (document_intelligence.py)
<details><summary>Probe PDF to estimate signal score and identify candidate pages.</summary>
<div class="doc-comment">
<p>Probe PDF to estimate signal score and identify candidate pages.</p>
<p></p>
<p>Args:</p>
<p>doc: PyMuPDF document object</p>
<p></p>
<p>Returns:</p>
<p>dict with "signal_score" (float) and "candidate_pages" (list[int])</p>
</div>
</details>
</li>
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
<li><code>crawl_onion</code> (dark_web_intelligence.py)
<details><summary>ISSUE-017: BFS crawl — bounded concurrency, Rust URL dedup.</summary>
<div class="doc-comment">
<p>ISSUE-017: BFS crawl — bounded concurrency, Rust URL dedup.</p>
<p></p>
<p>Replaces depth-first serial crawling with breadth-first parallel</p>
<p>processing using asyncio.Queue + parallel() bounded concurrency.</p>
<p></p>
<p>Pipeline: enqueue → parallel fetch → process results → enqueue new URLs</p>
<p>Rust MmapUrlSet (parking_lot::RwLock) for thread-safe URL dedup across coroutines.</p>
</div>
</details>
</li>
<li><code>_fetch_single_nvd</code> (exposure_clients.py)
<details><summary>Fetch CVEs for a single tech from NVD (rate-limited, cached).</summary>
<div class="doc-comment">
<p>Fetch CVEs for a single tech from NVD (rate-limited, cached).</p>
<p>Returns list of CVE dicts for yield.</p>
<p></p>
<p>ISSUE #016: Unified rate limiter interface — Rust NvdRateLimiter (token bucket)</p>
<p>or Python asyncio.Semaphore fallback.</p>
<p>- Rust try_acquire() non-blocking → cooperative async sleep loop</p>
<p>- Python Semaphore → async context manager</p>
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
<li><code>_extract_exif</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract EXIF data from image.</span></li>
<li><code>_execute_operation_async</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute intelligence operation asynchronously with per-host concurrency control.</span></li>
<li><code>cross_temporal_correlation</code> (temporal_archaeologist.py)
<details><summary>Find correlations between two entities across time.</summary>
<div class="doc-comment">
<p>Find correlations between two entities across time.</p>
<p></p>
<p>Args:</p>
<p>entity_a: First entity identifier</p>
<p>entity_b: Second entity identifier</p>
<p>id_type: Type of identifiers</p>
<p></p>
<p>Returns:</p>
<p>TemporalCorrelation with correlation analysis</p>
</div>
</details>
</li>
<li><code>query_host</code> (exposure_clients.py)
<details><summary>Query Shodan data pro danou IP.</summary>
<div class="doc-comment">
<p>Query Shodan data pro danou IP.</p>
<p></p>
<p>1. LMDB lookup (b"shodan:" + ip)</p>
<p>2. Cache hit → return cached data</p>
<p>3. Cache miss + SHODAN_API_KEY → HTTP GET api.shodan.io</p>
<p>4. Cache miss + no key → log INFO + return None</p>
<p></p>
<p>Returns:</p>
<p>dict s Shodan daty nebo None.</p>
</div>
</details>
</li>
<li><code>_predict_hidden_lsh</code> (relationship_discovery.py)</li>
<li><code>analyze_multiple_dumps_async</code> (document_intelligence.py)
<details><summary>Analyze multiple document dumps in parallel with optional cross-correlation.</summary>
<div class="doc-comment">
<p>Analyze multiple document dumps in parallel with optional cross-correlation.</p>
<p></p>
<p>Uses parallel() with concurrency=4 for M1-safe parallel processing.</p>
</div>
</details>
</li>
<li><code>find_sequential_patterns</code> (pattern_mining.py)
<details><summary>Find frequent sequential patterns using SPADE-like algorithm.</summary>
<div class="doc-comment">
<p>Find frequent sequential patterns using SPADE-like algorithm.</p>
<p></p>
<p>Args:</p>
<p>sequences: List of sequences (each sequence is a list of items)</p>
<p>min_support: Minimum support threshold (default: self.min_support)</p>
<p>max_pattern_length: Maximum length of patterns to find</p>
<p></p>
<p>Returns:</p>
<p>List of sequential patterns</p>
</div>
</details>
</li>
<li><code>search</code> (academic_search.py)
<details><summary>Execute multi-source academic search.</summary>
<div class="doc-comment">
<p>Execute multi-source academic search.</p>
<p></p>
<p>Args:</p>
<p>query: Original search query</p>
<p>max_results: Maximum total results to return</p>
<p>enable_expansion: Whether to expand the query (overrides default)</p>
<p>sources: List of source names to use (default: all)</p>
<p>async_session: Optional shared aiohttp session for connection pooling.</p>
<p>If provided, adapters reuse this session instead of</p>
<p>creating per-call sessions (reduces connection overhead).</p>
<p></p>
<p>Returns:</p>
<p>Academic search result</p>
</div>
</details>
</li>
<li><code>_execute_searches</code> (academic_search.py) — <span class="doc-comment-inline">Execute searches across all sources.</span></li>
<li><code>fetch_indeed_jobs</code> (web_intelligence.py) — <span class="doc-comment-inline">Fetch job postings from Indeed RSS.</span></li>
<li><code>discover_from_cooccurrence</code> (relationship_discovery.py)
<details><summary>Discover relationships from entity co-occurrence in documents.</summary>
<div class="doc-comment">
<p>Discover relationships from entity co-occurrence in documents.</p>
<p></p>
<p>Args:</p>
<p>documents: List of documents containing entity mentions</p>
<p>min_cooccurrence: Minimum co-occurrences to establish relationship</p>
<p>window_size: Optional context window size for co-occurrence</p>
<p></p>
<p>Returns:</p>
<p>List of discovered relationships</p>
</div>
</details>
</li>
<li><code>analyze</code> (document_intelligence.py)
<details><summary>Analyze any supported document type.</summary>
<div class="doc-comment">
<p>Analyze any supported document type.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to document file</p>
<p></p>
<p>Returns:</p>
<p>DocumentAnalysis with all extracted intelligence</p>
</div>
</details>
</li>
<li><code>_compute_fft_periodicity</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect periodicity using FFT (O(n log n) instead of O(n²) autocorrelation).</span></li>
<li><code>search</code> (academic_search.py)
<details><summary>Search Semantic Scholar for papers.</summary>
<div class="doc-comment">
<p>Search Semantic Scholar for papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>max_results: Maximum results to return</p>
<p>analysis: Optional query analysis</p>
<p>async_session: Optional shared aiohttp session for connection pooling.</p>
<p>If not provided, creates a per-call session (legacy behavior).</p>
</div>
</details>
</li>
<li><code>search_hosts</code> (exposure_clients.py)
<details><summary>Search Censys hosts.</summary>
<div class="doc-comment">
<p>Search Censys hosts.</p>
<p></p>
<p>1. LMDB lookup (b"censys:" + query)</p>
<p>2. Cache hit → return cached data</p>
<p>3. Cache miss + API credentials → HTTP POST to Censys API v2</p>
<p>4. Cache miss + no credentials → log INFO + return None</p>
<p></p>
<p>Returns:</p>
<p>list of host results nebo None.</p>
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
<li><code>query_common_crawl</code> (archive_discovery.py)
<details><summary>Query Common Crawl Index for URLs matching a domain.</summary>
<div class="doc-comment">
<p>Query Common Crawl Index for URLs matching a domain.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to search (e.g., "example.com")</p>
<p>limit: Maximum number of results to return</p>
<p></p>
<p>Returns:</p>
<p>List of CommonCrawlSnapshot objects</p>
</div>
</details>
</li>
<li><code>_parse_results</code> (academic_search.py) — <span class="doc-comment-inline">Parse ArXiv API XML response.</span></li>
<li><code>deep_historical_search</code> (temporal_archaeologist.py)
<details><summary>Perform deep historical search across archives.</summary>
<div class="doc-comment">
<p>Perform deep historical search across archives.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>time_range: Tuple of (start_date, end_date)</p>
<p>sources: List of sources to search</p>
<p></p>
<p>Returns:</p>
<p>List of archived versions matching query</p>
<p></p>
<p>ISSUE-003: Parallelized source search (was sequential for-loop).</p>
</div>
</details>
</li>
<li><code>search_semantic_scholar</code> (academic_discovery.py)
<details><summary>Search Semantic Scholar for academic papers.</summary>
<div class="doc-comment">
<p>Search Semantic Scholar for academic papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>max_results: Maximum results to return (default 10)</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with keys: title, authors, year, link</p>
</div>
</details>
</li>
<li><code>prepare_training_data</code> (relationship_discovery.py) — <span class="doc-comment-inline">Prepare training data for GNN.</span></li>
<li><code>_extract_entities</code> (workflow_orchestrator.py)
<details><summary>Extract IOCs (domains, IPs, hashes, URLs) from findings descriptions.</summary>
<div class="doc-comment">
<p>Extract IOCs (domains, IPs, hashes, URLs) from findings descriptions.</p>
<p></p>
<p>Returns:</p>
<p>(entities, domain_counts, ioc_counts)</p>
<p>domain_counts: domain -&gt; count across findings</p>
<p>ioc_counts: (value, type) -&gt; count across findings</p>
</div>
</details>
</li>
<li><code>discover_from_communications</code> (relationship_discovery.py)
<details><summary>Discover relationships from communication patterns.</summary>
<div class="doc-comment">
<p>Discover relationships from communication patterns.</p>
<p></p>
<p>Args:</p>
<p>communications: List of communication events</p>
<p>min_communications: Minimum communications to establish relationship</p>
<p>time_window_days: Optional time window for analysis</p>
<p></p>
<p>Returns:</p>
<p>List of discovered relationships</p>
</div>
</details>
</li>
<li><code>_recon_domain</code> (network_reconnaissance.py)
<details><summary>Reconnaissance for domain name.</summary>
<div class="doc-comment">
<p>Reconnaissance for domain name.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to recon</p>
<p>include_subdomains: Whether to brute force subdomains (default False for passive)</p>
</div>
</details>
</li>
<li><code>extract_ct_signals</code> (passive_fingerprint.py)
<details><summary>Extract CT (Certificate Transparency) metadata signals.</summary>
<div class="doc-comment">
<p>Extract CT (Certificate Transparency) metadata signals.</p>
<p></p>
<p>Returns dict with keys:</p>
<p>- cert_issuer: issuer organization</p>
<p>- cert_subject: subject organization</p>
<p>- all_names: all names from cert entries</p>
</div>
</details>
</li>
<li><code>_save_graph</code> (relationship_discovery.py)
<details><summary>Save the current graph to disk.</summary>
<div class="doc-comment">
<p>Save the current graph to disk.</p>
<p></p>
<p>Format policy (F-BLOOM-REGRESSION companion):</p>
<p>* igraph -&gt; ``write_picklez`` (igraph's native compact format, NOT</p>
<p>the Python ``pickle`` module — no exec surface).</p>
<p>* NetworkX -&gt; ``_graph_serde.save_nx_graph_jsonl`` (JSON via orjson,</p>
<p>zero-copy, no ``pickle`` interpreter surface).</p>
<p>* Fallback: igraph instance saved via JSON if available.</p>
<p></p>
<p>Both paths bounded, fail-soft. Never raises.</p>
</div>
</details>
</li>
<li><code>_detect_anomalies</code> (workflow_orchestrator.py)
<details><summary>Detect anomalies in module results.</summary>
<div class="doc-comment">
<p>Detect anomalies in module results.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p></p>
<p>Returns:</p>
<p>List of detected anomalies</p>
</div>
</details>
</li>
<li><code>enumerate_buckets</code> (exposed_service_hunter.py)
<details><summary>Enumerate S3 buckets using naming patterns.</summary>
<div class="doc-comment">
<p>Enumerate S3 buckets using naming patterns.</p>
<p></p>
<p>Args:</p>
<p>target: Target domain or company name</p>
<p>max_concurrent: Maximum concurrent requests</p>
<p></p>
<p>Returns:</p>
<p>List of exposed S3 buckets</p>
</div>
</details>
</li>
<li><code>calculate_risk_score</code> (blockchain_analyzer.py)
<details><summary>Calculate risk score for a wallet.</summary>
<div class="doc-comment">
<p>Calculate risk score for a wallet.</p>
<p></p>
<p>Args:</p>
<p>analysis: WalletAnalysis object</p>
<p></p>
<p>Returns:</p>
<p>Risk score between 0.0 (minimal) and 1.0 (critical)</p>
</div>
</details>
</li>
<li><code>search</code> (academic_search.py)
<details><summary>Search Crossref for academic papers.</summary>
<div class="doc-comment">
<p>Search Crossref for academic papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>max_results: Maximum results to return</p>
<p>analysis: Optional query analysis</p>
<p>async_session: Optional shared aiohttp session for connection pooling.</p>
<p>If not provided, creates a per-call session (legacy behavior).</p>
</div>
</details>
</li>
<li><code>search_arxiv</code> (academic_search.py) — <span class="doc-comment-inline">ArXiv API — security preprints. [{title, summary, published, link}]</span></li>
<li><code>_parse_content</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Parse HTML content and extract intelligence.</span></li>
<li><code>view_host</code> (exposure_clients.py)
<details><summary>View Censys host details.</summary>
<div class="doc-comment">
<p>View Censys host details.</p>
<p></p>
<p>1. LMDB lookup (censys:view:{ip})</p>
<p>2. Cache hit → return</p>
<p>3. Cache miss + API credentials → HTTP GET</p>
<p>4. Cache miss + no credentials → None</p>
</div>
</details>
</li>
<li><code>_extract_cert_info</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Extract information from certificate object.</span></li>
<li><code>add_relationship</code> (relationship_discovery.py)
<details><summary>Add a relationship between entities.</summary>
<div class="doc-comment">
<p>Add a relationship between entities.</p>
<p></p>
<p>Args:</p>
<p>relationship: Relationship to add</p>
<p></p>
<p>Returns:</p>
<p>True if added, False if already exists</p>
</div>
</details>
</li>
<li><code>find_matches</code> (identity_stitching.py)
<details><summary>Find potential matches for a profile.</summary>
<div class="doc-comment">
<p>Find potential matches for a profile.</p>
<p></p>
<p>Args:</p>
<p>profile_id: Profile ID to find matches for</p>
<p>min_score: Minimum match score (uses similarity_threshold if None)</p>
<p></p>
<p>Returns:</p>
<p>List of IdentityMatch objects sorted by score</p>
</div>
</details>
</li>
<li><code>get_snapshots</code> (archive_discovery.py)
<details><summary>Vrátí [{url, timestamp, statuscode, mimetype}] — max `limit` snapshotů.</summary>
<div class="doc-comment">
<p>Vrátí [{url, timestamp, statuscode, mimetype}] — max `limit` snapshotů.</p>
<p>Akceptuje URL i domain (auto-detekce podle wildcard syntaxe).</p>
<p>Bez externí session — vytváří vlastní.</p>
</div>
</details>
</li>
<li><code>execute_workflow</code> (workflow_orchestrator.py)
<details><summary>Execute a workflow plan.</summary>
<div class="doc-comment">
<p>Execute a workflow plan.</p>
<p></p>
<p>Args:</p>
<p>workflow: Workflow plan with module configuration</p>
<p>input_data: Input data for analysis</p>
<p></p>
<p>Returns:</p>
<p>Comprehensive analysis report</p>
</div>
</details>
</li>
<li><code>search</code> (academic_search.py)
<details><summary>Search ArXiv for papers.</summary>
<div class="doc-comment">
<p>Search ArXiv for papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>max_results: Maximum results to return</p>
<p>analysis: Optional query analysis</p>
<p>async_session: Optional shared aiohttp session for connection pooling.</p>
<p>If not provided, creates a per-call session (legacy behavior).</p>
</div>
</details>
</li>
<li><code>find_nodes_for_hash</code> (network_reconnaissance.py)
<details><summary>FIND_NODE query pro konkrétní info_hash.</summary>
<div class="doc-comment">
<p>FIND_NODE query pro konkrétní info_hash.</p>
<p>Vrátí list hostnames/IPs z DHT odpovědí.</p>
<p>M1: asyncio.DatagramEndpoint — čistě async UDP.</p>
</div>
</details>
</li>
<li><code>cleanup</code> (web_intelligence.py) — <span class="doc-comment-inline">Cleanup all system resources. Idempotent — safe to call multiple times.</span></li>
<li><code>search_cve</code> (exposure_clients.py)
<details><summary>Search GitHub code for CVE PoC samples.</summary>
<div class="doc-comment">
<p>Search GitHub code for CVE PoC samples.</p>
<p></p>
<p>Returns [{repo, url, path, stars}] — max 10 results.</p>
</div>
</details>
</li>
<li><code>analyze</code> (document_intelligence.py)
<details><summary>Analyze PDF document.</summary>
<div class="doc-comment">
<p>Analyze PDF document.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to PDF file, bytes, or file-like object</p>
<p></p>
<p>Returns:</p>
<p>DocumentAnalysis with all extracted data</p>
</div>
</details>
</li>
<li><code>get_identity_graph</code> (identity_stitching.py)
<details><summary>Get the identity graph with all profiles and matches.</summary>
<div class="doc-comment">
<p>Get the identity graph with all profiles and matches.</p>
<p></p>
<p>Returns:</p>
<p>igraph Graph with identity data (M1-optimized C-core)</p>
</div>
</details>
</li>
<li><code>example_usage</code> (identity_stitching.py) — <span class="doc-comment-inline">Example usage of the IdentityStitchingEngine.</span></li>
<li><code>_generate_recommendations</code> (workflow_orchestrator.py)
<details><summary>Generate actionable recommendations.</summary>
<div class="doc-comment">
<p>Generate actionable recommendations.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p>correlations: Correlation report</p>
<p>anomalies: Detected anomalies</p>
<p></p>
<p>Returns:</p>
<p>List of recommendation strings</p>
</div>
</details>
</li>
<li><code>scan_hosts</code> (exposed_service_hunter.py)
<details><summary>Scan hosts for exposed database ports.</summary>
<div class="doc-comment">
<p>Scan hosts for exposed database ports.</p>
<p></p>
<p>Args:</p>
<p>hosts: List of hostnames or IPs to scan</p>
<p>ports: Specific ports to check (default: all database ports)</p>
<p>max_concurrent: Maximum concurrent connections</p>
<p></p>
<p>Returns:</p>
<p>List of exposed database services</p>
</div>
</details>
</li>
<li><code>fetch_remoteok_jobs</code> (web_intelligence.py) — <span class="doc-comment-inline">Fetch from Remoteok.com API for remote job postings.</span></li>
<li><code>correlate_exposure_signals</code> (exposure_correlator.py)
<details><summary>F202C: Correlate asset exposure signals from sprint findings.</summary>
<div class="doc-comment">
<p>F202C: Correlate asset exposure signals from sprint findings.</p>
<p></p>
<p>Entry point for the exposure correlation sidecar.</p>
<p></p>
<p>Pipeline:</p>
<p>1. Extract signals from findings (ct_log, open_storage, jarm, passive_dns)</p>
<p>2. Correlate signals into ExposureFinding objects</p>
<p>3. Convert to CanonicalFinding list</p>
<p>4. Return for async_ingest_findings_batch ingestion</p>
<p></p>
<p>Bounds enforced:</p>
<p>- MAX_ASSETS=1000</p>
<p>- MAX_SIGNALS_PER_ASSET=3</p>
<p>- MAX_FINDINGS=500</p>
<p></p>
<p>Fail-soft: returns [] on any error.</p>
<p></p>
<p>Returns:</p>
<p>List of CanonicalFinding with source_type="exposure_correlation".</p>
</div>
</details>
</li>
<li><code>predict_hidden_connections</code> (relationship_discovery.py)</li>
<li><code>detect_change_points_wavelet</code> (pattern_mining.py)
<details><summary>Detect change points using wavelet decomposition.</summary>
<div class="doc-comment">
<p>Detect change points using wavelet decomposition.</p>
<p></p>
<p>Args:</p>
<p>series: Time series data</p>
<p></p>
<p>Returns:</p>
<p>List of change point indices</p>
</div>
</details>
</li>
<li><code>cross_chain_analysis</code> (blockchain_analyzer.py)
<details><summary>Perform cross-chain analysis.</summary>
<div class="doc-comment">
<p>Perform cross-chain analysis.</p>
<p></p>
<p>Args:</p>
<p>addresses: Dictionary mapping chain to address</p>
<p></p>
<p>Returns:</p>
<p>CrossChainResult with findings</p>
</div>
</details>
</li>
<li><code>_rank_results</code> (academic_search.py) — <span class="doc-comment-inline">Rank results by relevance (P1-3: parallel scoring with asyncio.to_thread).</span></li>
<li><code>extract_http_signals</code> (passive_fingerprint.py)
<details><summary>Extract HTTP-related signals from finding payload_text.</summary>
<div class="doc-comment">
<p>Extract HTTP-related signals from finding payload_text.</p>
<p></p>
<p>Returns dict with keys:</p>
<p>- server_headers: list of Server header values</p>
<p>- x_headers: list of X-* header values</p>
<p>- all_headers: combined header text for pattern matching</p>
<p>- html_content: HTML body if present</p>
</div>
</details>
</li>
<li><code>_build_igraph_graph</code> (relationship_discovery.py) — <span class="doc-comment-inline">Build igraph graph (M1 optimized, preferred over networkx when available).</span></li>
<li><code>probe</code> (document_intelligence.py)
<details><summary>Probe document to estimate value score for progressive parsing.</summary>
<div class="doc-comment">
<p>Probe document to estimate value score for progressive parsing.</p>
<p></p>
<p>Args:</p>
<p>url: Document URL</p>
<p>preview_bytes: Preview content bytes (first ~256KB)</p>
<p>query: Optional search query for semantic scoring</p>
<p></p>
<p>Returns:</p>
<p>dict with heuristic_score, semantic_score (if computed), final_score, keywords, entities</p>
</div>
</details>
</li>
<li><code>_detect_periodicity_mlx</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect periodicity using MLX FFT (M1 optimized).</span></li>
<li><code>query_domain</code> (exposed_service_hunter.py)
<details><summary>Query certificate transparency logs for a domain.</summary>
<div class="doc-comment">
<p>Query certificate transparency logs for a domain.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to query</p>
<p>include_subdomains: Include wildcard subdomains</p>
<p></p>
<p>Returns:</p>
<p>List of discovered subdomains</p>
</div>
</details>
</li>
<li><code>_cluster_by_temporal_correlation</code> (blockchain_analyzer.py)
<details><summary>Cluster by temporal correlation.</summary>
<div class="doc-comment">
<p>Cluster by temporal correlation.</p>
<p></p>
<p>Addresses with similar transaction timing patterns</p>
<p>may belong to the same entity.</p>
</div>
</details>
</li>
<li><code>_parse_results</code> (academic_search.py) — <span class="doc-comment-inline">Parse Crossref API JSON response.</span></li>
<li><code>search_pastebin</code> (open_source_collectors.py)</li>
<li><code>resolve_cname_chain</code> (network_reconnaissance.py)
<details><summary>Resolve full CNAME chain for a domain.</summary>
<div class="doc-comment">
<p>Resolve full CNAME chain for a domain.</p>
<p></p>
<p>Args:</p>
<p>domain: Starting domain</p>
<p>max_depth: Maximum resolution depth</p>
<p></p>
<p>Returns:</p>
<p>List of CNAMERecord objects forming the alias chain</p>
</div>
</details>
</li>
<li><code>fetch_hn_jobs</code> (web_intelligence.py) — <span class="doc-comment-inline">Fetch from Hacker News 'Who is Hiring' monthly threads via HN API.</span></li>
<li><code>_cve_lookup_background</code> (passive_fingerprint.py)
<details><summary>Background CVE lookup task — searches GitHub for PoC/exploit samples.</summary>
<div class="doc-comment">
<p>Background CVE lookup task — searches GitHub for PoC/exploit samples.</p>
<p></p>
<p>Stores results as CanonicalFinding with source_type="cve_lookup".</p>
<p>Fail-soft: logs and returns on any error.</p>
</div>
</details>
</li>
<li><code>fetch_cve_intelligence</code> (exposure_clients.py)
<details><summary>Fetch CVE intelligence for a tech stack.</summary>
<div class="doc-comment">
<p>Fetch CVE intelligence for a tech stack.</p>
<p></p>
<p>1. OSV.dev Batch API (priority) with streaming</p>
<p>2. NVD API 2.0 fallback (if OSV returns 0)</p>
<p>3. EPSS score enrichment per CVE</p>
<p></p>
<p>Yields dicts with CVE data + EPSS enrichment.</p>
<p>EPSS &gt;0.7 flags CVE as IMMEDIATE_ACTION.</p>
<p></p>
<p>Memory bounded: max 200 CVEs, batches of 20.</p>
<p>LMDB cache: 6h TTL for CVE data.</p>
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
<li><code>_predict_hidden_brute_force</code> (relationship_discovery.py)</li>
<li><code>_check_wayback</code> (archive_discovery.py) — <span class="doc-comment-inline">Check Wayback Machine CDX API for snapshots</span></li>
<li><code>search_ss</code> (academic_search.py) — <span class="doc-comment-inline">Semantic Scholar: [{title, abstract, year, doi, authors}]</span></li>
<li><code>search_repec</code> (open_source_collectors.py)</li>
<li><code>reconstruct_version_history</code> (temporal_archaeologist.py)
<details><summary>Reconstruct version history for an entity.</summary>
<div class="doc-comment">
<p>Reconstruct version history for an entity.</p>
<p></p>
<p>Args:</p>
<p>identifier: Entity identifier (URL, username, etc.)</p>
<p>id_type: Type of identifier (url, username, email, etc.)</p>
<p>from_date: Start date for reconstruction</p>
<p>to_date: End date for reconstruction</p>
<p></p>
<p>Returns:</p>
<p>EntityTimeline with reconstructed history</p>
</div>
</details>
</li>
<li><code>detect_temporal_anomalies</code> (temporal_archaeologist.py)
<details><summary>Detect temporal anomalies in a timeline.</summary>
<div class="doc-comment">
<p>Detect temporal anomalies in a timeline.</p>
<p></p>
<p>Args:</p>
<p>timeline: EntityTimeline to analyze</p>
<p></p>
<p>Returns:</p>
<p>List of detected anomalies</p>
</div>
</details>
</li>
<li><code>_crawl_task</code> (dark_web_intelligence.py)
<details><summary>ISSUE-017: Process single crawl task — fetch + extract links + enqueue new URLs.</summary>
<div class="doc-comment">
<p>ISSUE-017: Process single crawl task — fetch + extract links + enqueue new URLs.</p>
<p>Thread-safe: uses Rust MmapUrlSet (or OrderedDict fallback) for dedup.</p>
</div>
</details>
</li>
<li><code>_fetch_page</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Fetch a single page through Tor.</span></li>
<li><code>monitor_service</code> (dark_web_intelligence.py)
<details><summary>Continuously monitor a hidden service for changes.</summary>
<div class="doc-comment">
<p>Continuously monitor a hidden service for changes.</p>
<p></p>
<p>Args:</p>
<p>onion_address: .onion address to monitor</p>
<p>interval_minutes: Check interval in minutes</p>
<p></p>
<p>Yields:</p>
<p>Change notifications</p>
<p></p>
<p>Note:</p>
<p>Bounded by caller's iteration — caller MUST use ``async for``</p>
<p>or ``try/finally`` with ``aclose()`` to ensure cleanup on cancel.</p>
<p>``asyncio.CancelledError`` propagates from ``aclose()`` into the</p>
<p>``await asyncio.sleep()`` call, causing immediate loop termination.</p>
</div>
</details>
</li>
<li><code>_enrich_batch_epss</code> (exposure_clients.py)
<details><summary>ISSUE-003: Parallelize EPSS enrichment for a batch of CVEs.</summary>
<div class="doc-comment">
<p>ISSUE-003: Parallelize EPSS enrichment for a batch of CVEs.</p>
<p>Replaces sequential `await _enrich_epss` per CVE with parallel().</p>
<p>Returns list of CVEs with EPSS fields populated.</p>
</div>
</details>
</li>
<li><code>_make_exposed_host_finding</code> (exposure_correlator.py) — <span class="doc-comment-inline">Produce an exposed_host finding from an asset with bucket + cert/DNS.</span></li>
<li><code>example_usage</code> (relationship_discovery.py) — <span class="doc-comment-inline">Example usage of the RelationshipDiscoveryEngine.</span></li>
<li><code>search_url</code> (archive_discovery.py)
<details><summary>Search for archived versions of a URL.</summary>
<div class="doc-comment">
<p>Search for archived versions of a URL.</p>
<p></p>
<p>Args:</p>
<p>url: URL to search</p>
<p>sources: List of sources (wayback, archive_today, etc.)</p>
<p>limit_per_source: Maximum results per source</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of source -&gt; results</p>
</div>
</details>
</li>
<li><code>_analyze_ethereum_wallet</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Analyze Ethereum wallet using Etherscan.</span></li>
<li><code>_cluster_by_amount_patterns</code> (blockchain_analyzer.py)
<details><summary>Cluster by similar amount patterns.</summary>
<div class="doc-comment">
<p>Cluster by similar amount patterns.</p>
<p></p>
<p>Addresses with similar transaction amount distributions</p>
<p>may belong to the same entity.</p>
</div>
</details>
</li>
<li><code>_parse_results</code> (academic_search.py) — <span class="doc-comment-inline">Parse Semantic Scholar API JSON response.</span></li>
<li><code>search_rentry</code> (open_source_collectors.py)</li>
<li><code>_filter_private_ips_batch</code> (network_reconnaissance.py)
<details><summary>Batch-filter IPs using Rust batch_ip_classify.</summary>
<div class="doc-comment">
<p>Batch-filter IPs using Rust batch_ip_classify.</p>
<p></p>
<p>Returns (public_ips, private_ips) based on Rust classification.</p>
<p>Falls back to Python _is_private_ip if Rust unavailable.</p>
<p></p>
<p>Rust IpClass: 0=invalid, 1=private, 2=public, 3=loopback, 4=link-local</p>
<p>Private = class in (1, 3, 4) — Rust does same checks as Python _is_private_ip.</p>
</div>
</details>
</li>
<li><code>_detect_open_buckets_async</code> (exposure_correlator.py)
<details><summary>Async bucket enumeration for a single entity.</summary>
<div class="doc-comment">
<p>Async bucket enumeration for a single entity.</p>
<p></p>
<p>Uses lazy generator + semaphore(10) for parallel checks.</p>
<p>Returns list of accessible bucket dicts.</p>
</div>
</details>
</li>
<li><code>scan_open_storage</code> (exposure_correlator.py)
<details><summary>Scan domains for open storage buckets.</summary>
<div class="doc-comment">
<p>Scan domains for open storage buckets.</p>
<p></p>
<p>Returns list of OpenStorageResult for buckets returning HTTP 200.</p>
<p>Fail-soft: returns [] on any error.</p>
</div>
</details>
</li>
<li><code>__init__</code> (relationship_discovery.py)
<details><summary>Initialize the Relationship Discovery Engine.</summary>
<div class="doc-comment">
<p>Initialize the Relationship Discovery Engine.</p>
<p></p>
<p>Args:</p>
<p>use_sparse: Use scipy.sparse for large graphs (memory efficient)</p>
<p>max_memory_mb: ADVISORY ceiling in MB — not hard-enforced.</p>
<p>512MB recommended for M1 8GB UMA; 1024 is too aggressive.</p>
<p>enable_mlx: Enable MLX acceleration where available</p>
<p>lazy_evaluation: Defer expensive computations until needed</p>
</div>
</details>
</li>
<li><code>analyze</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze image file.</span></li>
<li><code>_extract_gps</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract GPS coordinates from EXIF.</span></li>
<li><code>mine_behavioral_patterns</code> (pattern_mining.py)
<details><summary>Mine behavioral patterns from user actions.</summary>
<div class="doc-comment">
<p>Mine behavioral patterns from user actions.</p>
<p></p>
<p>Args:</p>
<p>actions: List of user actions</p>
<p>min_actions: Minimum actions per user required</p>
<p></p>
<p>Returns:</p>
<p>List of detected behavioral patterns</p>
</div>
</details>
</li>
<li><code>compute_style_similarity</code> (identity_stitching.py)
<details><summary>Compute writing style similarity between two sets of texts.</summary>
<div class="doc-comment">
<p>Compute writing style similarity between two sets of texts.</p>
<p></p>
<p>Uses TF-IDF cosine similarity if sklearn is available,</p>
<p>falls back to simple lexical similarity.</p>
<p></p>
<p>Args:</p>
<p>texts1: First set of texts</p>
<p>texts2: Second set of texts</p>
<p></p>
<p>Returns:</p>
<p>Similarity score (0-1)</p>
</div>
</details>
</li>
<li><code>discover_endpoints</code> (exposed_service_hunter.py)
<details><summary>Discover GraphQL endpoints on a target.</summary>
<div class="doc-comment">
<p>Discover GraphQL endpoints on a target.</p>
<p></p>
<p>Args:</p>
<p>base_url: Base URL to scan</p>
<p>max_concurrent: Maximum concurrent requests</p>
<p></p>
<p>Returns:</p>
<p>List of discovered GraphQL endpoints</p>
</div>
</details>
</li>
<li><code>trace_transactions</code> (blockchain_analyzer.py)
<details><summary>Trace transaction chains from an address.</summary>
<div class="doc-comment">
<p>Trace transaction chains from an address.</p>
<p></p>
<p>Args:</p>
<p>address: Starting address</p>
<p>chain: Blockchain type</p>
<p>depth: How many hops to trace</p>
<p>max_transactions: Maximum transactions to return</p>
<p></p>
<p>Returns:</p>
<p>List of Transaction objects</p>
</div>
</details>
</li>
<li><code>analyze_certificate</code> (network_reconnaissance.py)
<details><summary>Analyze SSL certificate of remote host.</summary>
<div class="doc-comment">
<p>Analyze SSL certificate of remote host.</p>
<p></p>
<p>Args:</p>
<p>hostname: Host to connect to</p>
<p>port: Port (default 443)</p>
<p></p>
<p>Returns:</p>
<p>SSLCertificate or None</p>
</div>
</details>
</li>
<li><code>lookup_crtsh</code> (network_reconnaissance.py)
<details><summary>Query crt.sh Certificate Transparency log for domain certificates.</summary>
<div class="doc-comment">
<p>Query crt.sh Certificate Transparency log for domain certificates.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to search (e.g., "apple.com")</p>
<p>limit: Maximum number of results</p>
<p></p>
<p>Returns:</p>
<p>List of CTRawCertificate objects</p>
</div>
</details>
</li>
<li><code>__init__</code> (web_intelligence.py)</li>
<li><code>_group_similar_snapshots</code> (temporal_archaeologist.py)
<details><summary>Group similar snapshots using clustering.</summary>
<div class="doc-comment">
<p>Group similar snapshots using clustering.</p>
<p></p>
<p>ISSUE-026 FIX #3: Uses Rust rayon-parallel trigram Jaccard grouping</p>
<p>(text_similarity::group_similar_texts) when available — ~10-50× faster</p>
<p>than the serial Python SequenceMatcher O(n²) approach for large batches.</p>
<p>Falls back to pure-Python implementation if Rust extension unavailable.</p>
</div>
</details>
</li>
<li><code>_extract_links</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Extract .onion links from content.</span></li>
<li><code>find_cliques</code> (relationship_discovery.py)
<details><summary>Find cliques in the relationship graph.</summary>
<div class="doc-comment">
<p>Find cliques in the relationship graph.</p>
<p></p>
<p>Args:</p>
<p>min_size: Minimum clique size</p>
<p></p>
<p>Returns:</p>
<p>List of cliques (each clique is a list of entity IDs)</p>
</div>
</details>
</li>
<li><code>_ela_analysis_mps_sync</code> (document_intelligence.py) — <span class="doc-comment-inline">Synchronous MPS implementation of ELA.</span></li>
<li><code>_compute_semantic_score</code> (document_intelligence.py) — <span class="doc-comment-inline">Compute semantic similarity score between text and query using ModernBERT.</span></li>
<li><code>mine_temporal_patterns</code> (pattern_mining.py)
<details><summary>Mine temporal patterns from events.</summary>
<div class="doc-comment">
<p>Mine temporal patterns from events.</p>
<p></p>
<p>Args:</p>
<p>events: List of events with timestamps</p>
<p>min_events: Minimum number of events required</p>
<p></p>
<p>Returns:</p>
<p>List of detected temporal patterns</p>
</div>
</details>
</li>
<li><code>__init__</code> (identity_stitching.py)
<details><summary>Initialize the Identity Stitching Engine.</summary>
<div class="doc-comment">
<p>Initialize the Identity Stitching Engine.</p>
<p></p>
<p>Args:</p>
<p>similarity_threshold: Minimum similarity score for matching</p>
<p>signal_weights: Custom weights for match signals (uses defaults if None)</p>
<p>max_memory_mb: ADVISORY ceiling in MB — not hard-enforced.</p>
<p>Default 512MB is appropriate for M1 8GB UMA.</p>
<p>enable_fuzzy: Enable fuzzy string matching (requires rapidfuzz)</p>
</div>
</details>
</li>
<li><code>compute_username_similarity</code> (identity_stitching.py)
<details><summary>Compute similarity between two usernames.</summary>
<div class="doc-comment">
<p>Compute similarity between two usernames.</p>
<p></p>
<p>Uses rapidfuzz for fast fuzzy matching if available,</p>
<p>falls back to simple normalized comparison.</p>
<p></p>
<p>Args:</p>
<p>user1: First username</p>
<p>user2: Second username</p>
<p></p>
<p>Returns:</p>
<p>Similarity score (0-1)</p>
</div>
</details>
</li>
<li><code>_correlate_results</code> (workflow_orchestrator.py)
<details><summary>Correlate results across modules.</summary>
<div class="doc-comment">
<p>Correlate results across modules.</p>
<p></p>
<p>Args:</p>
<p>results: Dictionary of module results</p>
<p></p>
<p>Returns:</p>
<p>Correlation report with findings and risk score</p>
</div>
</details>
</li>
<li><code>detect_patterns</code> (blockchain_analyzer.py)
<details><summary>Detect suspicious patterns in transactions.</summary>
<div class="doc-comment">
<p>Detect suspicious patterns in transactions.</p>
<p></p>
<p>Args:</p>
<p>transactions: List of transactions to analyze</p>
<p></p>
<p>Returns:</p>
<p>List of detected TransactionPattern objects</p>
</div>
</details>
</li>
<li><code>brute_force_subdomains</code> (network_reconnaissance.py)
<details><summary>Brute force subdomains.</summary>
<div class="doc-comment">
<p>Brute force subdomains.</p>
<p></p>
<p>Returns:</p>
<p>List of (subdomain, ip, record_type) tuples</p>
</div>
</details>
</li>
<li><code>classify_ip</code> (exposure_clients.py) — <span class="doc-comment-inline">Vrátí {"ip", "classification", "name", "link", "noise", "riot"}</span></li>
<li><code>crack_dictionary</code> (cryptographic_intelligence.py)
<details><summary>Attempt dictionary attack on hash.</summary>
<div class="doc-comment">
<p>Attempt dictionary attack on hash.</p>
<p></p>
<p>Args:</p>
<p>hash_value: Hash to crack</p>
<p>wordlist: List of passwords to try (uses common passwords if None)</p>
<p>hash_type: Known hash type (auto-detect if None)</p>
<p></p>
<p>Returns:</p>
<p>Cracked password or None</p>
</div>
</details>
</li>
<li><code>search_openalex</code> (academic_discovery.py) — <span class="doc-comment-inline">Search OpenAlex for academic papers.</span></li>
<li><code>affinity_analysis</code> (relationship_discovery.py)
<details><summary>Perform affinity analysis on entities.</summary>
<div class="doc-comment">
<p>Perform affinity analysis on entities.</p>
<p></p>
<p>Args:</p>
<p>entity_type: Filter by entity type (None for all)</p>
<p>metric: Affinity metric (cooccurrence, jaccard, cosine)</p>
<p>use_mlx: Use MLX acceleration for similarity computation</p>
<p></p>
<p>Returns:</p>
<p>AffinityMatrix containing similarity scores</p>
</div>
</details>
</li>
<li><code>find_similar_chunks_mlx</code> (document_intelligence.py)
<details><summary>Find most similar chunks to query using MLX.</summary>
<div class="doc-comment">
<p>Find most similar chunks to query using MLX.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>top_k: Number of results to return</p>
<p></p>
<p>Returns:</p>
<p>List of (chunk_index, similarity_score) tuples</p>
</div>
</details>
</li>
<li><code>detect_change_points</code> (pattern_mining.py)
<details><summary>Detect change points in time series using wavelet + Mamba2 (with fallbacks).</summary>
<div class="doc-comment">
<p>Detect change points in time series using wavelet + Mamba2 (with fallbacks).</p>
<p></p>
<p>Uses:</p>
<p>1. Wavelet decomposition for change detection</p>
<p>2. Mamba2 forecasting for anomaly detection (best-effort)</p>
<p>3. EWMA/CUSUM fallbacks if MLX unavailable</p>
<p></p>
<p>Args:</p>
<p>series: Time series data</p>
<p></p>
<p>Returns:</p>
<p>List of change point indices</p>
</div>
</details>
</li>
<li><code>resurrect</code> (archive_discovery.py) — <span class="doc-comment-inline">Resurrect content from web archives.</span></li>
<li><code>_rate_limited_etherscan</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Make rate-limited Etherscan API call.</span></li>
<li><code>_rate_limited_blockchair</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Make rate-limited Blockchair API call.</span></li>
<li><code>_trigger_cve_lookup_tasks</code> (passive_fingerprint.py)
<details><summary>Fire background CVE lookup tasks for high-signal technologies.</summary>
<div class="doc-comment">
<p>Fire background CVE lookup tasks for high-signal technologies.</p>
<p></p>
<p>Triggers asyncio.create_task() for: WordPress, Drupal, Joomla, Typo3,</p>
<p>nginx, Apache, Next.js, React, Vue, Angular, Gatsby.</p>
<p></p>
<p>CVE results are stored via store.async_ingest_findings_batch().</p>
<p>Fail-safe: any error is logged and swallowed.</p>
</div>
</details>
</li>
<li><code>query_hash</code> (exposure_clients.py)
<details><summary>Query MalwareBazaar for file hash intelligence.</summary>
<div class="doc-comment">
<p>Query MalwareBazaar for file hash intelligence.</p>
<p></p>
<p>Returns raw MB response dict with query_status and data.</p>
</div>
</details>
</li>
<li><code>analyze</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze image content for steganography using semaphore pool.</span></li>
<li><code>_detect_trend</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect trend in event values or frequency.</span></li>
<li><code>mine_communication_patterns</code> (pattern_mining.py)
<details><summary>Mine communication patterns.</summary>
<div class="doc-comment">
<p>Mine communication patterns.</p>
<p></p>
<p>Args:</p>
<p>communications: List of communication events</p>
<p>min_communications: Minimum communications required</p>
<p></p>
<p>Returns:</p>
<p>List of detected communication patterns</p>
</div>
</details>
</li>
<li><code>find_all_matches</code> (identity_stitching.py)
<details><summary>Find all matches across all profiles — sync wrapper for CLI entry points.</summary>
<div class="doc-comment">
<p>Find all matches across all profiles — sync wrapper for CLI entry points.</p>
<p></p>
<p>ISSUE-005 FIX: Replaces asyncio.run() with asyncio.get_running_loop().run_until_complete()</p>
<p>which is safe on Python 3.14+ when called from a non-running-loop async context.</p>
<p>The try/except RuntimeError pattern above is retained for explicit error messaging</p>
<p>when called incorrectly from an active event loop.</p>
</div>
</details>
</li>
<li><code>__init__</code> (blockchain_analyzer.py)
<details><summary>Initialize BlockchainForensics.</summary>
<div class="doc-comment">
<p>Initialize BlockchainForensics.</p>
<p></p>
<p>Args:</p>
<p>etherscan_api_key: API key for Etherscan (Ethereum)</p>
<p>blockchair_api_key: API key for Blockchair (Bitcoin, others)</p>
<p>cache_ttl_seconds: Cache time-to-live in seconds (default: 300)</p>
<p>max_concurrent_requests: Max concurrent API requests (default: 5)</p>
<p>fetch_func: Optional async fetch function(url: str) -&gt; dict.</p>
<p>When provided, takes precedence over internal httpx client.</p>
<p>Enables canonical transport seam (circuit breaker, shared session).</p>
</div>
</details>
</li>
<li><code>identify_hash</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Identify possible hash types from hash string.</span></li>
<li><code>_build_adjacency_matrix</code> (relationship_discovery.py) — <span class="doc-comment-inline">Build adjacency matrix (sparse or dense).</span></li>
<li><code>_calculate_centrality_igraph</code> (relationship_discovery.py) — <span class="doc-comment-inline">Calculate centrality using igraph (M1 optimized).</span></li>
<li><code>_split_preview_into_chunks</code> (document_intelligence.py)
<details><summary>Split preview bytes into chunks for embedding.</summary>
<div class="doc-comment">
<p>Split preview bytes into chunks for embedding.</p>
<p></p>
<p>Args:</p>
<p>bytes_data: Preview bytes</p>
<p>max_chunks: Maximum number of chunks</p>
<p>max_tokens: Maximum tokens per chunk (approximated by word count)</p>
<p></p>
<p>Returns:</p>
<p>List of text chunks</p>
</div>
</details>
</li>
<li><code>chunk_text</code> (document_intelligence.py)
<details><summary>Split text into overlapping chunks with metadata.</summary>
<div class="doc-comment">
<p>Split text into overlapping chunks with metadata.</p>
<p></p>
<p>Args:</p>
<p>text: Large text to chunk</p>
<p>source: Source identifier (filename, URL, etc.)</p>
<p></p>
<p>Returns:</p>
<p>List of chunks with metadata</p>
</div>
</details>
</li>
<li><code>_detect_periodicity_autocorr</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect periodicity using autocorrelation.</span></li>
<li><code>_detect_bursts</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect burst patterns in event timing.</span></li>
<li><code>batch_pattern_matching</code> (pattern_mining.py)
<details><summary>Match patterns against data in batches (M1 memory optimized).</summary>
<div class="doc-comment">
<p>Match patterns against data in batches (M1 memory optimized).</p>
<p></p>
<p>Args:</p>
<p>patterns: Patterns to match</p>
<p>data_batch: Data to match against</p>
<p>batch_size: Size of processing batches</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping data index to matched patterns</p>
</div>
</details>
</li>
<li><code>_bounded_gather_pairs</code> (identity_stitching.py)</li>
<li><code>get_file_history</code> (archive_discovery.py) — <span class="doc-comment-inline">Get historical versions of a file from GitHub.</span></li>
<li><code>_execute_parallel</code> (workflow_orchestrator.py)
<details><summary>Execute modules in parallel groups.</summary>
<div class="doc-comment">
<p>Execute modules in parallel groups.</p>
<p></p>
<p>Args:</p>
<p>module_groups: Groups of modules to execute in parallel</p>
<p>input_data: Input data</p>
<p>context: Shared execution context</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of module results</p>
</div>
</details>
</li>
<li><code>_get_module_instance</code> (workflow_orchestrator.py)
<details><summary>Get module instance from registry or orchestrator.</summary>
<div class="doc-comment">
<p>Get module instance from registry or orchestrator.</p>
<p></p>
<p>Args:</p>
<p>module: Module name</p>
<p></p>
<p>Returns:</p>
<p>Module instance or None</p>
</div>
</details>
</li>
<li><code>_check_port</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if a specific port is open and identify service.</span></li>
<li><code>search_google_groups</code> (open_source_collectors.py)</li>
<li><code>enumerate_all</code> (network_reconnaissance.py)
<details><summary>Comprehensive DNS enumeration.</summary>
<div class="doc-comment">
<p>Comprehensive DNS enumeration.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to enumerate</p>
<p>include_subdomains: Whether to brute force subdomains</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with all DNS findings</p>
</div>
</details>
</li>
<li><code>permutation_scan</code> (network_reconnaissance.py)
<details><summary>Scan for subdomains using permutations.</summary>
<div class="doc-comment">
<p>Scan for subdomains using permutations.</p>
<p></p>
<p>Combines words with separators to find non-standard subdomains.</p>
</div>
</details>
</li>
<li><code>_parse_certificate</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Parse DER certificate.</span></li>
<li><code>_age_queued_priorities</code> (web_intelligence.py)
<details><summary>Age queued operations to improve priority over time.</summary>
<div class="doc-comment">
<p>Age queued operations to improve priority over time.</p>
<p></p>
<p>HARD EXIT: waits on shutdown event so task terminates immediately on cleanup.</p>
</div>
</details>
</li>
<li><code>compute_embeddings_mlx</code> (document_intelligence.py)
<details><summary>Compute MLX embeddings for chunks.</summary>
<div class="doc-comment">
<p>Compute MLX embeddings for chunks.</p>
<p></p>
<p>Args:</p>
<p>chunks: List of text chunks</p>
<p></p>
<p>Returns:</p>
<p>MLX array of embeddings or None if MLX unavailable</p>
</div>
</details>
</li>
<li><code>analyze_multiple_dumps</code> (document_intelligence.py)
<details><summary>Analyze multiple document dumps and optionally cross-correlate (sync wrapper).</summary>
<div class="doc-comment">
<p>Analyze multiple document dumps and optionally cross-correlate (sync wrapper).</p>
<p></p>
<p>Args:</p>
<p>dumps: Dict of {source_name: text_content}</p>
<p>cross_correlate: Whether to find links between dumps</p>
<p></p>
<p>Returns:</p>
<p>Dict of analyses per dump</p>
</div>
</details>
</li>
<li><code>search_across_dumps_async</code> (document_intelligence.py)
<details><summary>Search for query across multiple dumps using MLX similarity (parallel).</summary>
<div class="doc-comment">
<p>Search for query across multiple dumps using MLX similarity (parallel).</p>
<p></p>
<p>Uses parallel() with concurrency=4 for M1-safe parallel processing.</p>
</div>
</details>
</li>
<li><code>query_wayback</code> (archive_discovery.py)
<details><summary>Query Wayback Machine CDX API for snapshots of a URL.</summary>
<div class="doc-comment">
<p>Query Wayback Machine CDX API for snapshots of a URL.</p>
<p></p>
<p>Args:</p>
<p>url: URL to query</p>
<p>limit: Maximum number of snapshots to return</p>
<p></p>
<p>Returns:</p>
<p>List of WaybackSnapshot objects with snapshot URL and timestamp</p>
</div>
</details>
</li>
<li><code>to_markdown</code> (workflow_orchestrator.py)
<details><summary>Export report as Markdown string.</summary>
<div class="doc-comment">
<p>Export report as Markdown string.</p>
<p></p>
<p>Returns:</p>
<p>Markdown formatted report</p>
</div>
</details>
</li>
<li><code>test_mongodb_auth</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Test MongoDB for authentication requirements.</span></li>
<li><code>cluster_addresses</code> (blockchain_analyzer.py)
<details><summary>Cluster addresses using heuristics.</summary>
<div class="doc-comment">
<p>Cluster addresses using heuristics.</p>
<p></p>
<p>Args:</p>
<p>addresses: List of addresses to cluster</p>
<p>chain: Blockchain type</p>
<p></p>
<p>Returns:</p>
<p>List of Cluster objects</p>
</div>
</details>
</li>
<li><code>_deduplicate_results</code> (academic_search.py) — <span class="doc-comment-inline">Deduplicate results using deduplication engine.</span></li>
<li><code>lookup_asn</code> (network_reconnaissance.py)
<details><summary>Look up ASN information for IP address or prefix.</summary>
<div class="doc-comment">
<p>Look up ASN information for IP address or prefix.</p>
<p></p>
<p>Args:</p>
<p>ip_or_prefix: IP address (e.g., "8.8.8.8") or prefix (e.g., "8.8.8.0/24")</p>
<p></p>
<p>Returns:</p>
<p>List of ASNInfo objects</p>
</div>
</details>
</li>
<li><code>passive_dns_lookup</code> (network_reconnaissance.py)
<details><summary>Query Passive DNS service for domain resolution history.</summary>
<div class="doc-comment">
<p>Query Passive DNS service for domain resolution history.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to look up</p>
<p>api_key: Optional API key for dnslookupapi.com</p>
<p></p>
<p>Returns:</p>
<p>Dict with resolution records</p>
</div>
</details>
</li>
<li><code>new_identity</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Request new Tor identity (new exit node).</span></li>
<li><code>search_core</code> (academic_discovery.py) — <span class="doc-comment-inline">Search CORE.ac.uk for academic papers.</span></li>
<li><code>search_biorxiv</code> (academic_discovery.py) — <span class="doc-comment-inline">Search bioRxiv preprints.</span></li>
<li><code>search_medrxiv</code> (academic_discovery.py) — <span class="doc-comment-inline">Search medRxiv preprints.</span></li>
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
<li><code>predict_with_gnn</code> (relationship_discovery.py)
<details><summary>Použije GNN k predikci skrytých spojení.</summary>
<div class="doc-comment">
<p>Použije GNN k predikci skrytých spojení.</p>
<p></p>
<p>Args:</p>
<p>max_predictions: Maximální počet predikcí</p>
<p></p>
<p>Returns:</p>
<p>Seznam tuple (source_id, target_id, score)</p>
</div>
</details>
</li>
<li><code>get_network_stats</code> (relationship_discovery.py) — <span class="doc-comment-inline">Get comprehensive network statistics.</span></li>
<li><code>_extract_ooxml_core_props</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract core properties from OOXML.</span></li>
<li><code>compute_temporal_overlap</code> (identity_stitching.py)
<details><summary>Compute temporal overlap between two activity timelines.</summary>
<div class="doc-comment">
<p>Compute temporal overlap between two activity timelines.</p>
<p></p>
<p>Args:</p>
<p>activity1: First activity timeline</p>
<p>activity2: Second activity timeline</p>
<p>window_days: Time window for considering overlap</p>
<p></p>
<p>Returns:</p>
<p>Overlap score (0-1)</p>
</div>
</details>
</li>
<li><code>get_snapshots</code> (archive_discovery.py) — <span class="doc-comment-inline">Get list of snapshots for a URL.</span></li>
<li><code>_check_endpoint</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if a URL is a GraphQL endpoint with introspection enabled.</span></li>
<li><code>_cluster_by_common_input</code> (blockchain_analyzer.py)
<details><summary>Cluster by common input ownership.</summary>
<div class="doc-comment">
<p>Cluster by common input ownership.</p>
<p></p>
<p>If two addresses appear as inputs to the same transaction,</p>
<p>they likely belong to the same entity.</p>
</div>
</details>
</li>
<li><code>search_sec_edgar</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search SEC EDGAR full-text filings via EFTS API.</span></li>
<li><code>_execute_web_scraping</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute web scraping operations.</span></li>
<li><code>_enrich_epss</code> (exposure_clients.py)
<details><summary>Fetch EPSS score for a CVE.</summary>
<div class="doc-comment">
<p>Fetch EPSS score for a CVE.</p>
<p>Returns {"epss_score": float, "percentile": float} or None.</p>
</div>
</details>
</li>
<li><code>search_ia_scholar</code> (academic_discovery.py) — <span class="doc-comment-inline">Search Internet Archive Scholar for academic papers.</span></li>
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
<li><code>batch_analyze_async</code> (document_intelligence.py)
<details><summary>Analyze multiple documents in parallel (M1-safe, concurrency=8).</summary>
<div class="doc-comment">
<p>Analyze multiple documents in parallel (M1-safe, concurrency=8).</p>
<p></p>
<p>Uses parallel() with policy='collect' — all documents processed,</p>
<p>individual failures return None for that document without aborting others.</p>
</div>
</details>
</li>
<li><code>cross_reference_entities</code> (document_intelligence.py)
<details><summary>Find entities that appear across multiple documents.</summary>
<div class="doc-comment">
<p>Find entities that appear across multiple documents.</p>
<p></p>
<p>Args:</p>
<p>all_entities: All entities extracted from all documents</p>
<p></p>
<p>Returns:</p>
<p>List of cross-document links</p>
</div>
</details>
</li>
<li><code>reconstruct_timeline</code> (document_intelligence.py)
<details><summary>Reconstruct timeline from temporal entities.</summary>
<div class="doc-comment">
<p>Reconstruct timeline from temporal entities.</p>
<p></p>
<p>Args:</p>
<p>entities: Extracted entities</p>
<p>chunks: Document chunks</p>
<p></p>
<p>Returns:</p>
<p>List of timeline events</p>
</div>
</details>
</li>
<li><code>_detect_temporal_anomalies</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect anomalies in temporal pattern.</span></li>
<li><code>to_entities_and_relationships</code> (identity_stitching.py)
<details><summary>Convert stitched identities to Entity and Relationship objects.</summary>
<div class="doc-comment">
<p>Convert stitched identities to Entity and Relationship objects.</p>
<p></p>
<p>Args:</p>
<p>stitched_identities: Pre-computed stitched identities (optional)</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (entities, relationships) for RelationshipDiscoveryEngine</p>
</div>
</details>
</li>
<li><code>to_html</code> (workflow_orchestrator.py)
<details><summary>Export report as HTML string.</summary>
<div class="doc-comment">
<p>Export report as HTML string.</p>
<p></p>
<p>Returns:</p>
<p>HTML formatted report</p>
</div>
</details>
</li>
<li><code>_get_top_priority_pivots</code> (workflow_orchestrator.py)
<details><summary>Build bounded priority shortlist for operator.</summary>
<div class="doc-comment">
<p>Build bounded priority shortlist for operator.</p>
<p></p>
<p>Max 5 pivots. Prioritizes: infra-heavy, corroborated, high-severity.</p>
</div>
</details>
</li>
<li><code>_check_bucket_exists</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if an S3 bucket exists and is accessible.</span></li>
<li><code>scan_docker_apis</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Scan for exposed Docker APIs.</span></li>
<li><code>scan_kubernetes_apis</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Scan for exposed Kubernetes APIs.</span></li>
<li><code>analyze_wallet</code> (blockchain_analyzer.py)
<details><summary>Perform comprehensive wallet analysis.</summary>
<div class="doc-comment">
<p>Perform comprehensive wallet analysis.</p>
<p></p>
<p>Args:</p>
<p>address: Wallet address to analyze</p>
<p>chain: Blockchain type (ethereum, bitcoin, etc.)</p>
<p></p>
<p>Returns:</p>
<p>WalletAnalysis with comprehensive metrics</p>
</div>
</details>
</li>
<li><code>search_paste_gg</code> (open_source_collectors.py)</li>
<li><code>_extract_host</code> (web_intelligence.py)
<details><summary>Extract the primary host from a target's URLs.</summary>
<div class="doc-comment">
<p>Extract the primary host from a target's URLs.</p>
<p></p>
<p>Used by per-host gate to rate-limit concurrent operations per domain.</p>
<p></p>
<p>Args:</p>
<p>target: IntelligenceTarget with urls list</p>
<p></p>
<p>Returns:</p>
<p>Host string (e.g. "example.com") or empty string if no valid URL</p>
</div>
</details>
</li>
<li><code>temporal_entity_resolution</code> (temporal_archaeologist.py)
<details><summary>Resolve entity identity across multiple snapshots.</summary>
<div class="doc-comment">
<p>Resolve entity identity across multiple snapshots.</p>
<p></p>
<p>Args:</p>
<p>snapshots: List of archived versions</p>
<p>resolution_threshold: Minimum similarity for identity matching</p>
<p></p>
<p>Returns:</p>
<p>ResolvedEntity with canonical identity</p>
</div>
</details>
</li>
<li><code>run_passive_tech_stack_sidecar</code> (passive_fingerprint.py)
<details><summary>R11 async sidecar runner for passive tech-stack extraction.</summary>
<div class="doc-comment">
<p>R11 async sidecar runner for passive tech-stack extraction.</p>
<p></p>
<p>Returns count of stored findings.</p>
<p>Fail-soft: returns 0 on any error.</p>
<p></p>
<p>When tech_stack signals (CMS, web server, framework) are detected,</p>
<p>CVE lookup is triggered as asyncio.create_task() for significant technologies.</p>
</div>
</details>
</li>
<li><code>crawl_onion_legacy</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Legacy depth-first crawl (kept for backward compatibility).</span></li>
<li><code>get</code> (exposure_clients.py)
<details><summary>Synchroní LMDB get. Vrací cached data nebo None.</summary>
<div class="doc-comment">
<p>Synchroní LMDB get. Vrací cached data nebo None.</p>
<p>Kontroluje TTL.</p>
</div>
</details>
</li>
<li><code>rail_fence_decrypt</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Decrypt Rail Fence cipher.</span></li>
<li><code>auto_crack</code> (cryptographic_intelligence.py)
<details><summary>Automatically try to crack unknown classical cipher.</summary>
<div class="doc-comment">
<p>Automatically try to crack unknown classical cipher.</p>
<p></p>
<p>Tries multiple methods and returns best result.</p>
</div>
</details>
</li>
<li><code>analyze_security</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Analyze certificate security.</span></li>
<li><code>_deep_parse_pages</code> (document_intelligence.py)
<details><summary>Deep parse specific pages of the PDF.</summary>
<div class="doc-comment">
<p>Deep parse specific pages of the PDF.</p>
<p></p>
<p>Args:</p>
<p>doc: PyMuPDF document object</p>
<p>page_indices: List of page indices to parse</p>
<p></p>
<p>Returns:</p>
<p>List of extracted text strings for each page</p>
</div>
</details>
</li>
<li><code>_compute_heuristic_score</code> (document_intelligence.py) — <span class="doc-comment-inline">Compute heuristic value score based on content analysis.</span></li>
<li><code>_extract_snapshot</code> (archive_discovery.py) — <span class="doc-comment-inline">Extract content from a single snapshot</span></li>
<li><code>search</code> (archive_discovery.py)
<details><summary>Search GitHub code using advanced operators.</summary>
<div class="doc-comment">
<p>Search GitHub code using advanced operators.</p>
<p>Example: "leaked password" language:python extension:env</p>
</div>
</details>
</li>
<li><code>_derive_theme_key</code> (workflow_orchestrator.py) — <span class="doc-comment-inline">Derive theme key from finding for grouping.</span></li>
<li><code>test_redis_auth</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Test Redis for authentication requirements.</span></li>
<li><code>search_gmane</code> (open_source_collectors.py)</li>
<li><code>fetch_room_messages</code> (open_source_collectors.py)</li>
<li><code>search_court_records</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search federal court cases via CourtListener API.</span></li>
<li><code>_detect_frequency_shifts</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect shifts in update frequency.</span></li>
<li><code>_crawl_single_onion</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Crawl a single onion address and return results list (for parallel()).</span></li>
<li><code>analyze</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Analyze data to detect encryption.</span></li>
<li><code>predict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Predict hidden relationships.</span></li>
<li><code>search_across_dumps</code> (document_intelligence.py)
<details><summary>Search for query across multiple dumps using MLX similarity (sync wrapper).</summary>
<div class="doc-comment">
<p>Search for query across multiple dumps using MLX similarity (sync wrapper).</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>dumps: Dict of {source_name: text_content}</p>
<p>top_k_per_dump: Number of results per dump</p>
<p></p>
<p>Returns:</p>
<p>Dict of search results per dump</p>
</div>
</details>
</li>
<li><code>_detect_seasonality</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect daily/weekly seasonality patterns.</span></li>
<li><code>_maybe_evict_on_pressure</code> (identity_stitching.py) — <span class="doc-comment-inline">Evict 50% of cache if RSS exceeds memory pressure threshold.</span></li>
<li><code>_execute_sequential</code> (workflow_orchestrator.py)
<details><summary>Execute modules sequentially.</summary>
<div class="doc-comment">
<p>Execute modules sequentially.</p>
<p></p>
<p>Args:</p>
<p>modules: List of module names</p>
<p>input_data: Input data</p>
<p>context: Shared execution context</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of module results</p>
</div>
</details>
</li>
<li><code>_generate_report</code> (workflow_orchestrator.py)
<details><summary>Generate comprehensive report.</summary>
<div class="doc-comment">
<p>Generate comprehensive report.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p>correlations: Correlation report</p>
<p>anomalies: Detected anomalies</p>
<p>context: Shared execution context</p>
<p></p>
<p>Returns:</p>
<p>Comprehensive analysis report</p>
</div>
</details>
</li>
<li><code>_check_kubernetes_api</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if a Kubernetes API is exposed.</span></li>
<li><code>build_dedup_items</code> (academic_search.py) — <span class="doc-comment-inline">Build all DedupItems from search results (Pass 1).</span></li>
<li><code>__init__</code> (temporal_archaeologist.py)
<details><summary>Initialize TemporalArchaeologist.</summary>
<div class="doc-comment">
<p>Initialize TemporalArchaeologist.</p>
<p></p>
<p>Args:</p>
<p>max_concurrent_requests: Maximum concurrent archive requests</p>
<p>request_timeout: Timeout for archive requests in seconds</p>
<p>cache_enabled: Whether to cache results</p>
<p>max_content_size_mb: Maximum content size to process in MB</p>
</div>
</details>
</li>
<li><code>_recover_from_archive_today</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Archive.today.</span></li>
<li><code>initialize</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Initialize Tor proxy connection.</span></li>
<li><code>_resolve_cname_chain</code> (exposure_correlator.py)
<details><summary>Resolve CNAME chain for a subdomain.</summary>
<div class="doc-comment">
<p>Resolve CNAME chain for a subdomain.</p>
<p></p>
<p>Returns list of CNAME targets in chain order.</p>
</div>
</details>
</li>
<li><code>_detect_subdomain_takeover_async</code> (exposure_correlator.py)
<details><summary>Async subdomain takeover detection.</summary>
<div class="doc-comment">
<p>Async subdomain takeover detection.</p>
<p></p>
<p>Uses PassiveDNSResolver to follow CNAME chains and checks for</p>
<p>takeover-vulnerable providers.</p>
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
<li><code>add_entity</code> (relationship_discovery.py)
<details><summary>Add an entity to the engine.</summary>
<div class="doc-comment">
<p>Add an entity to the engine.</p>
<p></p>
<p>Args:</p>
<p>entity: Entity to add</p>
<p></p>
<p>Returns:</p>
<p>True if added, False if already exists</p>
</div>
</details>
</li>
<li><code>_get_forensics_pool</code> (document_intelligence.py)
<details><summary>Get or create the shared forensics ProcessPoolExecutor (M1 8GB safe: max_workers=2).</summary>
<div class="doc-comment">
<p>Get or create the shared forensics ProcessPoolExecutor (M1 8GB safe: max_workers=2).</p>
<p></p>
<p>Uses spawn context on macOS to avoid fork issues with MPS/Swift libraries.</p>
<p>Fail-safe: returns ThreadPoolExecutor fallback if ProcessPool creation fails.</p>
</div>
</details>
</li>
<li><code>_analyze_ooxml</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze Office Open XML format (docx, xlsx, pptx).</span></li>
<li><code>add_profile</code> (identity_stitching.py)
<details><summary>Add an identity profile to the engine.</summary>
<div class="doc-comment">
<p>Add an identity profile to the engine.</p>
<p></p>
<p>Args:</p>
<p>profile: IdentityProfile to add</p>
<p></p>
<p>Returns:</p>
<p>True if added, False if already exists</p>
</div>
</details>
</li>
<li><code>remove_profile</code> (identity_stitching.py) — <span class="doc-comment-inline">Remove a profile and all its indexes.</span></li>
<li><code>get_snapshot_content</code> (archive_discovery.py) — <span class="doc-comment-inline">Get content of a specific snapshot.</span></li>
<li><code>_build_confidence_note</code> (workflow_orchestrator.py) — <span class="doc-comment-inline">Human-readable confidence explanation.</span></li>
<li><code>__init__</code> (exposed_service_hunter.py)
<details><summary>Initialize API cache.</summary>
<div class="doc-comment">
<p>Initialize API cache.</p>
<p></p>
<p>Args:</p>
<p>cache_dir: Directory for cache DB (default: temp)</p>
<p>ttl_seconds: Cache TTL in seconds (default: 1 hour)</p>
</div>
</details>
</li>
<li><code>get</code> (exposed_service_hunter.py)
<details><summary>Get cached value if not expired.</summary>
<div class="doc-comment">
<p>Get cached value if not expired.</p>
<p></p>
<p>Args:</p>
<p>key: Cache key</p>
<p></p>
<p>Returns:</p>
<p>Cached value or None if expired/missing</p>
</div>
</details>
</li>
<li><code>_cached_request</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Make a cached API request. F184F: LRU eviction when cache exceeds MAX_CACHE_SIZE.</span></li>
<li><code>_detect_peel_chain</code> (blockchain_analyzer.py)
<details><summary>Detect peel chain pattern.</summary>
<div class="doc-comment">
<p>Detect peel chain pattern.</p>
<p></p>
<p>A peel chain is a series of transactions where:</p>
<p>1. A large amount is sent</p>
<p>2. Change is returned to a new address</p>
<p>3. Process repeats</p>
</div>
</details>
</li>
<li><code>identify_known_services</code> (blockchain_analyzer.py)
<details><summary>Identify known services associated with an address.</summary>
<div class="doc-comment">
<p>Identify known services associated with an address.</p>
<p></p>
<p>Args:</p>
<p>address: Wallet address</p>
<p></p>
<p>Returns:</p>
<p>List of service tags</p>
</div>
</details>
</li>
<li><code>get_citations</code> (academic_search.py) — <span class="doc-comment-inline">Get papers that cite this paper.</span></li>
<li><code>score_and_maybe_keep</code> (academic_search.py) — <span class="doc-comment-inline">Consumer function: score item, check dedup, return if unique.</span></li>
<li><code>_initialize_components</code> (web_intelligence.py) — <span class="doc-comment-inline">Initialize all intelligence components.</span></li>
<li><code>to_canonical_findings</code> (passive_fingerprint.py)
<details><summary>Convert ServiceFingerprint list to CanonicalFinding list.</summary>
<div class="doc-comment">
<p>Convert ServiceFingerprint list to CanonicalFinding list.</p>
<p></p>
<p>Each CanonicalFinding:</p>
<p>- source_type = "passive_fingerprint"</p>
<p>- finding_id = "pfp_{hash}"</p>
<p>- payload_text = JSON with fingerprint data + facets envelope</p>
</div>
</details>
</li>
<li><code>set</code> (exposure_clients.py)
<details><summary>Synchroní LMDB set. Vrací True při úspěchu.</summary>
<div class="doc-comment">
<p>Synchroní LMDB set. Vrací True při úspěchu.</p>
<p>Single-writer přes DB_EXECUTOR.</p>
</div>
</details>
</li>
<li><code>extract_iocs</code> (exposure_clients.py)
<details><summary>Extract IOCs from MalwareBazaar response.</summary>
<div class="doc-comment">
<p>Extract IOCs from MalwareBazaar response.</p>
<p></p>
<p>Returns [(value, ioc_type)] tuples including:</p>
<p>- sha256, md5, sha1 hashes</p>
<p>- imphash</p>
<p>- malware family tags</p>
<p>- C2 IPs from vendor_intel</p>
</div>
</details>
</li>
<li><code>_mlx_similarity_matrix</code> (relationship_discovery.py) — <span class="doc-comment-inline">Compute similarity matrix using MLX acceleration.</span></li>
<li><code>_extract_frequency_pattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Extract frequency-based behavioral pattern.</span></li>
<li><code>detect_anomalies_in_pattern</code> (pattern_mining.py)
<details><summary>Detect anomalies relative to an established pattern.</summary>
<div class="doc-comment">
<p>Detect anomalies relative to an established pattern.</p>
<p></p>
<p>Args:</p>
<p>pattern: Established pattern to compare against</p>
<p>new_data: New data points to check</p>
<p>threshold: Standard deviation threshold for anomaly detection</p>
<p></p>
<p>Returns:</p>
<p>List of detected anomalies</p>
</div>
</details>
</li>
<li><code>_detect_behavioral_anomalies</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect anomalies in behavioral pattern.</span></li>
<li><code>cross_pattern_correlation</code> (pattern_mining.py)
<details><summary>Calculate correlations between patterns.</summary>
<div class="doc-comment">
<p>Calculate correlations between patterns.</p>
<p></p>
<p>Args:</p>
<p>patterns: List of patterns to correlate</p>
<p>use_mlx: Whether to use MLX acceleration</p>
<p></p>
<p>Returns:</p>
<p>CorrelationMatrix with pairwise correlations</p>
</div>
</details>
</li>
<li><code>_build_so_what</code> (workflow_orchestrator.py) — <span class="doc-comment-inline">Build one-liner operator takeaway.</span></li>
<li><code>get_paper_details</code> (academic_search.py) — <span class="doc-comment-inline">Get detailed information about a specific paper.</span></li>
<li><code>lookup</code> (network_reconnaissance.py)
<details><summary>Perform WHOIS lookup.</summary>
<div class="doc-comment">
<p>Perform WHOIS lookup.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to lookup</p>
<p></p>
<p>Returns:</p>
<p>WHOISData or None if lookup fails</p>
</div>
</details>
</li>
<li><code>memory_posture</code> (web_intelligence.py) — <span class="doc-comment-inline">Read-only seam: memory pressure state for M1 8GB.</span></li>
<li><code>_recover_from_git_history</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Git history.</span></li>
<li><code>reset_session</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Clear all session state (bounded structures + queues).</span></li>
<li><code>dht_content_to_canonical</code> (dark_web_intelligence.py)
<details><summary>Sprint F214Q: Map DHT crawl result → CanonicalFinding for sprint ingestion.</summary>
<div class="doc-comment">
<p>Sprint F214Q: Map DHT crawl result → CanonicalFinding for sprint ingestion.</p>
<p></p>
<p>Bounded: payload_text truncated to 3000 chars, fail-safe.</p>
<p>INVARIANT: DHT queries NEVER go over Tor — clearnet UDP only.</p>
</div>
</details>
</li>
<li><code>_fetch_nvd_fallback</code> (exposure_clients.py)
<details><summary>NVD API 2.0 fallback - parallelized with bounded concurrency.</summary>
<div class="doc-comment">
<p>NVD API 2.0 fallback - parallelized with bounded concurrency.</p>
<p></p>
<p>ISSUE-003: Replaced sequential `for tech in tech_stack` with parallel().</p>
<p>Yields CVEs as they complete (not in order) for better UX.</p>
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
<li><code>_numpy_similarity_matrix</code> (relationship_discovery.py) — <span class="doc-comment-inline">Compute similarity matrix using NumPy.</span></li>
<li><code>_extract_pdf_objects</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract embedded objects from PDF.</span></li>
<li><code>_ensure_stegdetect</code> (document_intelligence.py) — <span class="doc-comment-inline">Compile and install stegdetect if missing.</span></li>
<li><code>_detect_flow_anomalies</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect anomalies in flow pattern.</span></li>
<li><code>_extract_pattern_features</code> (pattern_mining.py) — <span class="doc-comment-inline">Extract numerical features from patterns for correlation.</span></li>
<li><code>compute_network_overlap</code> (identity_stitching.py)
<details><summary>Compute network overlap (shared connections).</summary>
<div class="doc-comment">
<p>Compute network overlap (shared connections).</p>
<p></p>
<p>Args:</p>
<p>network1: First network (set of connection IDs)</p>
<p>network2: Second network (set of connection IDs)</p>
<p></p>
<p>Returns:</p>
<p>Overlap score (0-1)</p>
</div>
</details>
</li>
<li><code>_find_campaign_hints</code> (workflow_orchestrator.py)
<details><summary>Find findings that may belong to the same campaign.</summary>
<div class="doc-comment">
<p>Find findings that may belong to the same campaign.</p>
<p></p>
<p>Heuristic: same type appearing from multiple sources or</p>
<p>high confidence + high severity cluster.</p>
</div>
</details>
</li>
<li><code>_get_corroborated_iocs</code> (workflow_orchestrator.py)
<details><summary>Return IOCs that appear with 2+ source evidence.</summary>
<div class="doc-comment">
<p>Return IOCs that appear with 2+ source evidence.</p>
<p></p>
<p>Corroborated = repeated across findings + high severity + high confidence.</p>
</div>
</details>
</li>
<li><code>_build_operator_shortlist</code> (workflow_orchestrator.py)
<details><summary>Build max-3 bounded prioritised shortlist for scheduler/export.</summary>
<div class="doc-comment">
<p>Build max-3 bounded prioritised shortlist for scheduler/export.</p>
<p></p>
<p>Returns items with: action, target, rationale (scheduler-consumable shape).</p>
<p></p>
<p>Scheduler transformation: action=query, target=rationale[:80], rationale=pivot_type</p>
</div>
</details>
</li>
<li><code>get_certificate_details</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Get detailed certificate information from CT logs.</span></li>
<li><code>get_paper_details</code> (academic_search.py) — <span class="doc-comment-inline">Get detailed information about a specific paper.</span></li>
<li><code>get_work_by_doi</code> (academic_search.py) — <span class="doc-comment-inline">Get detailed information about a work by DOI.</span></li>
<li><code>attempt_zone_transfer</code> (network_reconnaissance.py)
<details><summary>Attempt DNS zone transfer (AXFR).</summary>
<div class="doc-comment">
<p>Attempt DNS zone transfer (AXFR).</p>
<p></p>
<p>Returns:</p>
<p>List of zone records if successful, None otherwise</p>
</div>
</details>
</li>
<li><code>graph_add_ip_asn_relations</code> (network_reconnaissance.py)
<details><summary>FÁZE P9: Add IP→ASN relations to GraphManager.</summary>
<div class="doc-comment">
<p>FÁZE P9: Add IP→ASN relations to GraphManager.</p>
<p></p>
<p>Streamované přidávání — voláno po ASN lookup.</p>
</div>
</details>
</li>
<li><code>_ensure_components_initialized</code> (web_intelligence.py)
<details><summary>Lazy initialization — spustí komponenty a aging task pouze jednou při první operaci.</summary>
<div class="doc-comment">
<p>Lazy initialization — spustí komponenty a aging task pouze jednou při první operaci.</p>
<p></p>
<p>Uses lock to prevent race condition when multiple operations race to init.</p>
</div>
</details>
</li>
<li><code>_get_spacy_matcher</code> (web_intelligence.py) — <span class="doc-comment-inline">Lazy spaCy PhraseMatcher initialization.</span></li>
<li><code>check_source</code> (temporal_archaeologist.py)</li>
<li><code>_recover_from_google_cache</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Google Cache.</span></li>
<li><code>_recover_from_bing_cache</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Bing Cache via jina.ai.</span></li>
<li><code>run_passive_fingerprint_sidecar</code> (passive_fingerprint.py)
<details><summary>Async sidecar runner for passive fingerprinting.</summary>
<div class="doc-comment">
<p>Async sidecar runner for passive fingerprinting.</p>
<p></p>
<p>Returns count of stored findings.</p>
</div>
</details>
</li>
<li><code>_map_ecosystem</code> (exposure_clients.py)
<details><summary>Map package name/tech stack entry to (ecosystem, package_name).</summary>
<div class="doc-comment">
<p>Map package name/tech stack entry to (ecosystem, package_name).</p>
<p>Returns (ecosystem, package_name) tuple.</p>
</div>
</details>
</li>
<li><code>_score_english</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Score how likely text is English (0-1).</span></li>
<li><code>to_canonical_findings</code> (exposure_correlator.py)
<details><summary>Convert ExposureFinding list to CanonicalFinding list.</summary>
<div class="doc-comment">
<p>Convert ExposureFinding list to CanonicalFinding list.</p>
<p></p>
<p>Each CanonicalFinding:</p>
<p>- source_type = "exposure_correlation"</p>
<p>- finding_id = "exp_{hash}"</p>
<p>- payload_text = JSON with correlation data + evidence envelope fields</p>
</div>
</details>
</li>
<li><code>_run_sync</code> (academic_discovery.py)
<details><summary>Run an async function synchronously in an isolated event loop.</summary>
<div class="doc-comment">
<p>Run an async function synchronously in an isolated event loop.</p>
<p></p>
<p>Accepts an async function and its arguments — does NOT accept a pre-created</p>
<p>coroutine. This ensures the loop check happens BEFORE any coroutine is</p>
<p>instantiated, preventing RuntimeWarning: coroutine was never awaited.</p>
<p></p>
<p>Raises RuntimeError if called from a running event loop — in that case,</p>
<p>the async function should be awaited directly.</p>
</div>
</details>
</li>
<li><code>get_relationships</code> (relationship_discovery.py)
<details><summary>Get relationships, optionally filtered by entity or type.</summary>
<div class="doc-comment">
<p>Get relationships, optionally filtered by entity or type.</p>
<p></p>
<p>Args:</p>
<p>entity_id: Filter by source entity</p>
<p>relationship_type: Filter by relationship type</p>
<p></p>
<p>Returns:</p>
<p>List of matching relationships</p>
</div>
</details>
</li>
<li><code>_build_networkx_graph</code> (relationship_discovery.py) — <span class="doc-comment-inline">Build NetworkX graph (lazy evaluation).</span></li>
<li><code>predict_hidden_connections_fast</code> (relationship_discovery.py)
<details><summary>DEPRECATED: Use predict_hidden_connections(method='fast') instead.</summary>
<div class="doc-comment">
<p>DEPRECATED: Use predict_hidden_connections(method='fast') instead.</p>
<p></p>
<p>Args:</p>
<p>max_predictions: Maximum number of predictions to return</p>
<p></p>
<p>Returns:</p>
<p>List of (source_id, target_id, score) tuples sorted by score desc.</p>
</div>
</details>
</li>
<li><code>_build_entity_vectors</code> (relationship_discovery.py) — <span class="doc-comment-inline">Build feature vectors for entities based on their relationships.</span></li>
<li><code>save_graph</code> (relationship_discovery.py)
<details><summary>Persist NetworkX graph to disk with node-count pruning.</summary>
<div class="doc-comment">
<p>Persist NetworkX graph to disk with node-count pruning.</p>
<p></p>
<p>Uses ``_graph_serde.save_nx_graph_jsonl`` (JSON via orjson, no</p>
<p>Python ``pickle``). Bounded, fail-soft.</p>
</div>
</details>
</li>
<li><code>_parse_gps</code> (document_intelligence.py) — <span class="doc-comment-inline">Parse GPS data from EXIF.</span></li>
<li><code>close</code> (document_intelligence.py)
<details><summary>Close all resources including thread pool and stegdetect processes.</summary>
<div class="doc-comment">
<p>Close all resources including thread pool and stegdetect processes.</p>
<p></p>
<p>Called synchronously from __del__ (GC context) — no async allowed.</p>
<p>Stegdetect processes are killed outright (no restart needed on shutdown).</p>
</div>
</details>
</li>
<li><code>extract_entities</code> (document_intelligence.py)
<details><summary>Extract entities from text using pattern matching.</summary>
<div class="doc-comment">
<p>Extract entities from text using pattern matching.</p>
<p></p>
<p>Args:</p>
<p>text: Text to analyze</p>
<p>source: Source document</p>
<p>chunk_id: Chunk identifier</p>
<p></p>
<p>Returns:</p>
<p>List of extracted entities</p>
</div>
</details>
</li>
<li><code>__init__</code> (pattern_mining.py)
<details><summary>Initialize pattern mining engine.</summary>
<div class="doc-comment">
<p>Initialize pattern mining engine.</p>
<p></p>
<p>Args:</p>
<p>max_memory_mb: ADVISORY ceiling in MB for M1 8GB UMA (512 recommended).</p>
<p>Not hard-enforced — rely on specific bounded structures.</p>
<p>use_mlx: Whether to use MLX acceleration on M1</p>
<p>min_support: Minimum support threshold for patterns (0-1)</p>
<p>min_confidence: Minimum confidence threshold for patterns (0-1)</p>
</div>
</details>
</li>
<li><code>_extract_action_sequence</code> (pattern_mining.py) — <span class="doc-comment-inline">Extract common action sequences using sequential pattern mining.</span></li>
<li><code>_analyze_network_structure</code> (pattern_mining.py) — <span class="doc-comment-inline">Analyze overall network structure.</span></li>
<li><code>_correlation_mlx</code> (pattern_mining.py) — <span class="doc-comment-inline">Calculate correlation using MLX (M1 optimized).</span></li>
<li><code>get_identity_communities</code> (identity_stitching.py)
<details><summary>Detect communities in the identity graph.</summary>
<div class="doc-comment">
<p>Detect communities in the identity graph.</p>
<p></p>
<p>Returns:</p>
<p>List of communities (sets of profile IDs) using igraph C-core</p>
</div>
</details>
</li>
<li><code>get_recent_pastes</code> (archive_discovery.py) — <span class="doc-comment-inline">Fetch recent public pastes.</span></li>
<li><code>to_json</code> (workflow_orchestrator.py)
<details><summary>Export report as JSON string.</summary>
<div class="doc-comment">
<p>Export report as JSON string.</p>
<p></p>
<p>Returns:</p>
<p>JSON formatted report string</p>
</div>
</details>
</li>
<li><code>_check_docker_api</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if a Docker API is exposed.</span></li>
<li><code>scan_container_apis</code> (exposed_service_hunter.py)
<details><summary>Scan for exposed Docker and Kubernetes APIs.</summary>
<div class="doc-comment">
<p>Scan for exposed Docker and Kubernetes APIs.</p>
<p></p>
<p>Args:</p>
<p>hosts: List of hostnames or IPs</p>
<p></p>
<p>Returns:</p>
<p>List of exposed container APIs</p>
</div>
</details>
</li>
<li><code>_analyze_bitcoin_wallet</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Analyze Bitcoin wallet using Blockchair.</span></li>
<li><code>_detect_mixing_patterns</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Detect potential mixing/tumbling patterns.</span></li>
<li><code>_merge_clusters</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Merge overlapping clusters.</span></li>
<li><code>_simple_deduplicate</code> (academic_search.py) — <span class="doc-comment-inline">Simple deduplication based on URL and title.</span></li>
<li><code>search_academic</code> (academic_search.py)
<details><summary>Convenience function for academic search.</summary>
<div class="doc-comment">
<p>Convenience function for academic search.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>max_results: Maximum results to return</p>
<p>enable_expansion: Whether to expand the query</p>
<p></p>
<p>Returns:</p>
<p>Search results</p>
</div>
</details>
</li>
<li><code>_process_next_queued_operation</code> (web_intelligence.py) — <span class="doc-comment-inline">Process the next queued operation after current one completes.</span></li>
<li><code>_execute_threat_assessment</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute threat assessment.</span></li>
<li><code>encode_decode</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Encode/decode various encodings.</span></li>
<li><code>_detect_open_buckets</code> (exposure_correlator.py)
<details><summary>Sync wrapper for bucket enumeration.</summary>
<div class="doc-comment">
<p>Sync wrapper for bucket enumeration.</p>
<p></p>
<p>Returns list of bucket findings (sync, for integration with existing pipeline).</p>
<p>Uses get_running_loop + run_until_complete, falls back to new_event_loop only</p>
<p>when no loop is running (GHOST_INVARIANTS compliant).</p>
</div>
</details>
</li>
<li><code>_detect_subdomain_takeover</code> (exposure_correlator.py)
<details><summary>Sync wrapper for subdomain takeover detection.</summary>
<div class="doc-comment">
<p>Sync wrapper for subdomain takeover detection.</p>
<p></p>
<p>Returns list of takeover findings.</p>
<p>Uses get_running_loop + run_until_complete, falls back to new_event_loop only</p>
<p>when no loop is running (GHOST_INVARIANTS compliant).</p>
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
<li><code>flag_manipulated_image</code> (relationship_discovery.py)
<details><summary>S49-C: Flag manipulated image in graph and reduce credibility.</summary>
<div class="doc-comment">
<p>S49-C: Flag manipulated image in graph and reduce credibility.</p>
<p></p>
<p>Args:</p>
<p>url: URL of the manipulated image</p>
<p>ela_score: ELA score (0-1, higher = more likely manipulated)</p>
</div>
</details>
</li>
<li><code>_get_mamba_model</code> (pattern_mining.py) — <span class="doc-comment-inline">Get or load Mamba2 model (lazy).</span></li>
<li><code>_build_lsh_fingerprint</code> (identity_stitching.py) — <span class="doc-comment-inline">Build 64-bit SimHash fingerprint pro LSH candidate pre-filtering.</span></li>
<li><code>wayback_cdx_lookup</code> (archive_discovery.py)
<details><summary>Compat: Wayback CDX lookup pro deep_research_sources.py call-site.</summary>
<div class="doc-comment">
<p>Compat: Wayback CDX lookup pro deep_research_sources.py call-site.</p>
<p>AUTHORITY: Canonical implementation je WaybackCDX.get_snapshots().</p>
<p>Tato funkce je dočasný compat wrapper — neměň její return format,</p>
<p>dokud nebudou všechny call-sites přesměrovány.</p>
<p></p>
<p>Returns:</p>
<p>List of dicts s klíči: title, url, snippet, backend, rank, provider, source, timestamp</p>
</div>
</details>
</li>
<li><code>_check_pattern</code> (workflow_orchestrator.py)
<details><summary>Check if a pattern exists in results.</summary>
<div class="doc-comment">
<p>Check if a pattern exists in results.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p>pattern: Pattern to check (module, indicator)</p>
<p></p>
<p>Returns:</p>
<p>True if pattern detected</p>
</div>
</details>
</li>
<li><code>_extract_indicators</code> (workflow_orchestrator.py)
<details><summary>Extract suspicious indicators from results.</summary>
<div class="doc-comment">
<p>Extract suspicious indicators from results.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p></p>
<p>Returns:</p>
<p>List of indicator strings</p>
</div>
</details>
</li>
<li><code>_extract_attribution</code> (workflow_orchestrator.py)
<details><summary>Extract attribution information from results.</summary>
<div class="doc-comment">
<p>Extract attribution information from results.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p></p>
<p>Returns:</p>
<p>Attribution dictionary</p>
</div>
</details>
</li>
<li><code>_get_verdict</code> (workflow_orchestrator.py)
<details><summary>Determine verdict based on risk score.</summary>
<div class="doc-comment">
<p>Determine verdict based on risk score.</p>
<p></p>
<p>Args:</p>
<p>risk_score: Calculated risk score (0.0-1.0)</p>
<p></p>
<p>Returns:</p>
<p>Verdict string ("CLEAN", "SUSPICIOUS", or "HIGH_RISK")</p>
</div>
</details>
</li>
<li><code>detect_transaction_patterns</code> (blockchain_analyzer.py)
<details><summary>Convenience function for pattern detection.</summary>
<div class="doc-comment">
<p>Convenience function for pattern detection.</p>
<p></p>
<p>Args:</p>
<p>address: Starting address</p>
<p>chain: Blockchain type</p>
<p>depth: Trace depth</p>
<p>etherscan_api_key: Etherscan API key</p>
<p>blockchair_api_key: Blockchair API key</p>
<p></p>
<p>Returns:</p>
<p>List of TransactionPattern</p>
</div>
</details>
</li>
<li><code>execute_search</code> (academic_search.py) — <span class="doc-comment-inline">Execute search with performance tracking.</span></li>
<li><code>score_one</code> (academic_search.py) — <span class="doc-comment-inline">Compute relevance_score for one result (CPU-bound).</span></li>
<li><code>_execute_operation_type</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute specific operation type.</span></li>
<li><code>_check_snapshot_available</code> (temporal_archaeologist.py)
<details><summary>Check if a Wayback snapshot is available via HEAD request (Fix 1).</summary>
<div class="doc-comment">
<p>Check if a Wayback snapshot is available via HEAD request (Fix 1).</p>
<p></p>
<p>Args:</p>
<p>wayback_url: URL to check</p>
<p></p>
<p>Returns:</p>
<p>True if snapshot is available (status 200)</p>
</div>
</details>
</li>
<li><code>_detect_activity_gaps</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect unusual gaps in activity.</span></li>
<li><code>_detect_temporal_gaps</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect temporal gaps in snapshots.</span></li>
<li><code>_crawl_depth_parallel</code> (dark_web_intelligence.py)
<details><summary>ISSUE-003: Parallelize crawling of multiple links at the same depth.</summary>
<div class="doc-comment">
<p>ISSUE-003: Parallelize crawling of multiple links at the same depth.</p>
<p>Uses bounded concurrency (max 3 concurrent Tor requests) for rate safety.</p>
</div>
</details>
</li>
<li><code>vigenere_crack</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Crack Vigenere cipher using Kasiski examination and frequency analysis.</span></li>
<li><code>remove_entity</code> (relationship_discovery.py) — <span class="doc-comment-inline">Remove an entity and all its relationships.</span></li>
<li><code>_extract_pdf_metadata</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract PDF metadata.</span></li>
<li><code>_parse_pdf_date</code> (document_intelligence.py) — <span class="doc-comment-inline">Parse PDF date string format.</span></li>
<li><code>_analyze_communication_pair</code> (pattern_mining.py) — <span class="doc-comment-inline">Analyze communication pattern between a specific pair.</span></li>
<li><code>detect_periodicity_mlx</code> (pattern_mining.py)
<details><summary>Detect periodicity using MLX FFT (public API).</summary>
<div class="doc-comment">
<p>Detect periodicity using MLX FFT (public API).</p>
<p></p>
<p>Args:</p>
<p>timestamps: List of timestamps</p>
<p>values: Optional values associated with timestamps</p>
<p></p>
<p>Returns:</p>
<p>List of detected temporal patterns with periodicity</p>
</div>
</details>
</li>
<li><code>_assess_quality</code> (archive_discovery.py) — <span class="doc-comment-inline">Assess content quality (0.0-1.0)</span></li>
<li><code>_calc_cross_source_confidence</code> (workflow_orchestrator.py)
<details><summary>Calculate 0.0-1.0 multi-source corroboration confidence.</summary>
<div class="doc-comment">
<p>Calculate 0.0-1.0 multi-source corroboration confidence.</p>
<p></p>
<p>Signal: same IOC/indicator seen across multiple independent sources.</p>
</div>
</details>
</li>
<li><code>_get_what_matters_first</code> (workflow_orchestrator.py) — <span class="doc-comment-inline">Return single primary action/takeaway for operator.</span></li>
<li><code>check_graphql_introspection</code> (exposed_service_hunter.py)
<details><summary>Check GraphQL endpoint for introspection.</summary>
<div class="doc-comment">
<p>Check GraphQL endpoint for introspection.</p>
<p></p>
<p>Args:</p>
<p>endpoint: GraphQL endpoint URL</p>
<p></p>
<p>Returns:</p>
<p>Introspection result or None</p>
</div>
</details>
</li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>search_public_rooms</code> (open_source_collectors.py)</li>
<li><code>search_ssrn</code> (open_source_collectors.py)</li>
<li><code>check_subdomain</code> (network_reconnaissance.py)</li>
<li><code>_query_whois_server</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Query specific WHOIS server.</span></li>
<li><code>recon_target</code> (network_reconnaissance.py)
<details><summary>Perform complete reconnaissance on target.</summary>
<div class="doc-comment">
<p>Perform complete reconnaissance on target.</p>
<p></p>
<p>Args:</p>
<p>target: Domain or IP address</p>
<p>include_subdomains: Whether to brute force subdomains (default False for passive)</p>
<p></p>
<p>Returns:</p>
<p>HostInfo with all gathered intelligence</p>
</div>
</details>
</li>
<li><code>pivot_domain</code> (network_reconnaissance.py)
<details><summary>Domain → IPs → buffer to IOC graph.</summary>
<div class="doc-comment">
<p>Domain → IPs → buffer to IOC graph.</p>
<p></p>
<p>Returns count of new IOCs buffered.</p>
</div>
</details>
</li>
<li><code>_match_server_header</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Match a Server header value against known patterns.</span></li>
<li><code>__init__</code> (dark_web_intelligence.py)</li>
<li><code>caesar_bruteforce</code> (cryptographic_intelligence.py)
<details><summary>Brute-force all 25 Caesar shifts and score results.</summary>
<div class="doc-comment">
<p>Brute-force all 25 Caesar shifts and score results.</p>
<p></p>
<p>Returns ranked list of possible solutions.</p>
</div>
</details>
</li>
<li><code>_calculate_entropy</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Calculate Shannon entropy of string.</span></li>
<li><code>_detect_charset</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Detect character set used in hash.</span></li>
<li><code>_guess_cipher</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Guess possible cipher type.</span></li>
<li><code>_check_bucket_head</code> (exposure_correlator.py)
<details><summary>Perform HEAD check on a single bucket URL.</summary>
<div class="doc-comment">
<p>Perform HEAD check on a single bucket URL.</p>
<p></p>
<p>Returns dict with bucket info if accessible (200/403), None if unreachable.</p>
</div>
</details>
</li>
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
<li><code>_idx_discard</code> (relationship_discovery.py)
<details><summary>Pop key from `index` if present, regardless of underlying type.</summary>
<div class="doc-comment">
<p>Pop key from `index` if present, regardless of underlying type.</p>
<p></p>
<p>Works for both `set` (.discard) and `OrderedDict` (pop with default).</p>
</div>
</details>
</li>
<li><code>export_for_visualization</code> (relationship_discovery.py) — <span class="doc-comment-inline">Export graph data optimized for visualization.</span></li>
<li><code>_basic_pdf_analysis</code> (document_intelligence.py) — <span class="doc-comment-inline">Fallback basic analysis without PyMuPDF.</span></li>
<li><code>_parse_core_xml</code> (document_intelligence.py) — <span class="doc-comment-inline">Parse core.xml properties.</span></li>
<li><code>_run_async</code> (document_intelligence.py)
<details><summary>Run an async coroutine in a separate thread with its own event loop.</summary>
<div class="doc-comment">
<p>Run an async coroutine in a separate thread with its own event loop.</p>
<p></p>
<p>This avoids asyncio.run() crash on M1 and prevents blocking MLX workers.</p>
</div>
</details>
</li>
<li><code>_ingest_pattern</code> (pattern_mining.py)
<details><summary>Ingest a pattern for heavy hitters tracking.</summary>
<div class="doc-comment">
<p>Ingest a pattern for heavy hitters tracking.</p>
<p></p>
<p>Args:</p>
<p>pattern_id: Unique identifier for the pattern</p>
</div>
</details>
</li>
<li><code>_read_text_with_cap</code> (archive_discovery.py) — <span class="doc-comment-inline">Read response text with payload cap for M1 RAM safety.</span></li>
<li><code>search</code> (archive_discovery.py) — <span class="doc-comment-inline">Search for archived versions on Archive.today.</span></li>
<li><code>fetch_content</code> (archive_discovery.py) — <span class="doc-comment-inline">Fetch content from IPFS by CID.</span></li>
<li><code>initialize</code> (archive_discovery.py) — <span class="doc-comment-inline">Initialize security components and HTTP session</span></li>
<li><code>_detect_content_type</code> (archive_discovery.py) — <span class="doc-comment-inline">Detect content type from MIME type</span></li>
<li><code>discover_from_wayback</code> (archive_discovery.py)
<details><summary>Discover historical endpoints from Wayback Machine.</summary>
<div class="doc-comment">
<p>Discover historical endpoints from Wayback Machine.</p>
<p>COMPAT: Tato funkce je archive-discovery wrapper kolem WaybackCDX.</p>
<p>AUTHORITY: WaybackCDX.get_snapshots() je nízkoúrovňový interface.</p>
<p>REMOVAL CONDITION: pokud by se měl tento wrapper odstranit,</p>
<p>všechny call-sites přejdou přímo na WaybackCDX.</p>
</div>
</details>
</li>
<li><code>filter_by_keyword</code> (archive_discovery.py)
<details><summary>Fetch recent pastes and filter by keyword.</summary>
<div class="doc-comment">
<p>Fetch recent pastes and filter by keyword.</p>
<p>Used for credential/component leak detection.</p>
</div>
</details>
</li>
<li><code>_find_coupling_pairs</code> (workflow_orchestrator.py)
<details><summary>Find entity pairs that appear in the same finding.</summary>
<div class="doc-comment">
<p>Find entity pairs that appear in the same finding.</p>
<p></p>
<p>Returns list of (entity1_value, entity2_value) tuples.</p>
</div>
</details>
</li>
<li><code>_calc_campaign_confidence</code> (workflow_orchestrator.py)
<details><summary>Calculate 0.0-1.0 campaign cluster confidence.</summary>
<div class="doc-comment">
<p>Calculate 0.0-1.0 campaign cluster confidence.</p>
<p></p>
<p>Evidence: multi_source_cluster hints + overlapping themes across sources.</p>
</div>
</details>
</li>
<li><code>_fetch_bitcoin_transactions</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Fetch Bitcoin transactions from Blockchair.</span></li>
<li><code>analyze_blockchain_address</code> (blockchain_analyzer.py)
<details><summary>Convenience function for quick address analysis.</summary>
<div class="doc-comment">
<p>Convenience function for quick address analysis.</p>
<p></p>
<p>Args:</p>
<p>address: Wallet address</p>
<p>chain: Blockchain type</p>
<p>etherscan_api_key: Etherscan API key</p>
<p>blockchair_api_key: Blockchair API key</p>
<p></p>
<p>Returns:</p>
<p>WalletAnalysis</p>
</div>
</details>
</li>
<li><code>_scrape_paste_gg</code> (open_source_collectors.py)</li>
<li><code>query_records</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Query specific DNS record type.</span></li>
<li><code>_execute_osint_collection</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute OSINT collection operations.</span></li>
<li><code>_normalize_seniority</code> (web_intelligence.py) — <span class="doc-comment-inline">Infer seniority distribution from job posting text.</span></li>
<li><code>example_usage</code> (web_intelligence.py) — <span class="doc-comment-inline">Example usage of the unified intelligence system.</span></li>
<li><code>_find_overlapping_periods</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Find overlapping time periods between two timelines.</span></li>
<li><code>_match_http_headers</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Match HTTP headers against known service patterns.</span></li>
<li><code>_match_tls_cert</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Match TLS/certificate text against known patterns.</span></li>
<li><code>_match_ct_metadata</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Match CT metadata against known service patterns.</span></li>
<li><code>_match_html_content</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Match HTML content against known service patterns.</span></li>
<li><code>darkweb_content_to_canonical</code> (dark_web_intelligence.py)
<details><summary>Sprint F251: Map DarkWebCrawler output → CanonicalFinding for sprint ingestion.</summary>
<div class="doc-comment">
<p>Sprint F251: Map DarkWebCrawler output → CanonicalFinding for sprint ingestion.</p>
<p></p>
<p>Bounded: payload_text truncated to 3000 chars, fail-safe if title is None.</p>
</div>
</details>
</li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>vigenere_decrypt</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Decrypt Vigenere cipher with given key.</span></li>
<li><code>enumerate_cloud_buckets</code> (exposure_correlator.py)
<details><summary>Enumerate S3/GCP/Azure buckets for an entity name.</summary>
<div class="doc-comment">
<p>Enumerate S3/GCP/Azure buckets for an entity name.</p>
<p></p>
<p>Uses lazy generator with semaphore(10) for parallel HEAD checks.</p>
<p>Returns list of bucket findings with provider, status, and severity.</p>
<p></p>
<p>Bounds:</p>
<p>- MAX_BUCKET_CANDIDATES_PER_ENTITY=30 candidates max</p>
<p>- MAX_BUCKET_CHECKS_PARALLEL=10 parallel checks</p>
<p>- 200 = OPEN BUCKET (HIGH severity), 403 = bucket exists (MEDIUM)</p>
</div>
</details>
</li>
<li><code>detect_subdomain_takeovers</code> (exposure_correlator.py)
<details><summary>Detect subdomain takeover vulnerabilities.</summary>
<div class="doc-comment">
<p>Detect subdomain takeover vulnerabilities.</p>
<p></p>
<p>Uses PassiveDNSResolver to follow CNAME chains and identifies</p>
<p>subdomains pointing to takeover-vulnerable providers.</p>
<p></p>
<p>Returns list of takeover findings with severity=CRITICAL.</p>
<p></p>
<p>Bounds:</p>
<p>- MAX_SUBDOMAIN_TAKEOVER_SUBDOMAINS=50 subdomains per entity</p>
</div>
</details>
</li>
<li><code>enable_gnn</code> (relationship_discovery.py)
<details><summary>Inicializuje GNN prediktor a spustí trénink na pozadí, pokud je graf dostatečně velký.</summary>
<div class="doc-comment">
<p>Inicializuje GNN prediktor a spustí trénink na pozadí, pokud je graf dostatečně velký.</p>
<p></p>
<p>Args:</p>
<p>scheduler: Volitelný scheduler pro background training</p>
</div>
</details>
</li>
<li><code>_adamic_adar</code> (relationship_discovery.py) — <span class="doc-comment-inline">Compute Adamic/Adar score for non-adjacent vertices.</span></li>
<li><code>_merge_foca_metadata</code> (document_intelligence.py)
<details><summary>Merge FOCA metadata into DocumentAnalysis return value.</summary>
<div class="doc-comment">
<p>Merge FOCA metadata into DocumentAnalysis return value.</p>
<p></p>
<p>FOCA data goes into metadata.raw_metadata['foca'] — different seam from TriageFacets.</p>
</div>
</details>
</li>
<li><code>__init__</code> (document_intelligence.py)
<details><summary>Initialize MLX Long-Context Analyzer.</summary>
<div class="doc-comment">
<p>Initialize MLX Long-Context Analyzer.</p>
<p></p>
<p>Args:</p>
<p>chunk_size: Tokens per chunk (default 4096 for M1 8GB)</p>
<p>overlap: Overlap between chunks for context continuity</p>
</div>
</details>
</li>
<li><code>add</code> (pattern_mining.py) — <span class="doc-comment-inline">Add item to window.</span></li>
<li><code>_matches_pattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Check if item matches pattern (simplified).</span></li>
<li><code>create_pattern_mining_engine</code> (pattern_mining.py)
<details><summary>Factory function for creating PatternMiningEngine.</summary>
<div class="doc-comment">
<p>Factory function for creating PatternMiningEngine.</p>
<p></p>
<p>Args:</p>
<p>max_memory_mb: Maximum memory usage in MB</p>
<p>use_mlx: Whether to use MLX acceleration on M1</p>
<p>min_support: Minimum support threshold for patterns</p>
<p>min_confidence: Minimum confidence threshold for patterns</p>
<p></p>
<p>Returns:</p>
<p>Configured PatternMiningEngine instance</p>
</div>
</details>
</li>
<li><code>_index_profile_fields</code> (identity_stitching.py) — <span class="doc-comment-inline">Index username/email/alias/platform fields into reverse maps. Idempotent.</span></li>
<li><code>_check_social_archive</code> (archive_discovery.py) — <span class="doc-comment-inline">Check social media archives</span></li>
<li><code>fetch_snapshot_text</code> (archive_discovery.py)
<details><summary>Stáhnout text konkrétního snapshotu pro PatternMatcher scan.</summary>
<div class="doc-comment">
<p>Stáhnout text konkrétního snapshotu pro PatternMatcher scan.</p>
<p>URL format: https://web.archive.org/web/{timestamp}/{original_url}</p>
</div>
</details>
</li>
<li><code>_extract_primary_entity</code> (workflow_orchestrator.py) — <span class="doc-comment-inline">Extract primary IOC entity from finding description.</span></li>
<li><code>_detect_round_amounts</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Detect round amount patterns (common in exchange withdrawals).</span></li>
<li><code>_do_search</code> (academic_search.py)</li>
<li><code>_paste_cache_get</code> (open_source_collectors.py)
<details><summary>Returns (hit, body). Body is meaningful only when hit=True.</summary>
<div class="doc-comment">
<p>Returns (hit, body). Body is meaningful only when hit=True.</p>
<p></p>
<p>TTL-expired entries are evicted lazily on read.</p>
</div>
</details>
</li>
<li><code>search_biorxiv</code> (open_source_collectors.py)</li>
<li><code>search_medrxiv</code> (open_source_collectors.py)</li>
<li><code>_is_private_ip</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Check if IP is private/reserved using ipaddress module (not regex).</span></li>
<li><code>bootstrap_nodes</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Resolve bootstrap nodes přes DNS.</span></li>
<li><code>_execute_vulnerability_analysis</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute vulnerability analysis.</span></li>
<li><code>_fetch_wayback_content</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Fetch content from Wayback Machine URL.</span></li>
<li><code>should_skip_runs</code> (passive_fingerprint.py)
<details><summary>Determine if passive fingerprinting should be skipped due to RAM pressure.</summary>
<div class="doc-comment">
<p>Determine if passive fingerprinting should be skipped due to RAM pressure.</p>
<p></p>
<p>Args:</p>
<p>ram_percent: current RSS as percentage of total</p>
<p>high_water: high water mark threshold</p>
<p></p>
<p>Returns:</p>
<p>True if should skip (ram_percent &gt; 85% AND high_water is critical)</p>
</div>
</details>
</li>
<li><code>_osv_to_cve</code> (exposure_clients.py) — <span class="doc-comment-inline">Convert OSV vulnerability format to our CVE dict.</span></li>
<li><code>_chi_square_score</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Calculate chi-square statistic against English frequencies.</span></li>
<li><code>_estimate_complexity</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Estimate cracking complexity.</span></li>
<li><code>_generate_bucket_candidates</code> (exposure_correlator.py)
<details><summary>Generate lazy bucket name candidates for an entity.</summary>
<div class="doc-comment">
<p>Generate lazy bucket name candidates for an entity.</p>
<p></p>
<p>Yields suffix-augmented names for S3-style buckets.</p>
<p>Generator pattern: yields tuples of (candidate_name, provider, url_template).</p>
</div>
</details>
</li>
<li><code>to_dict</code> (academic_discovery.py) — <span class="doc-comment-inline">Convert to dictionary.</span></li>
<li><code>cleanup</code> (dns_tunnel_detector.py)
<details><summary>Clean up detector resources.</summary>
<div class="doc-comment">
<p>Clean up detector resources.</p>
<p></p>
<p>Releases memory used by the LSTM model and clears caches.</p>
</div>
</details>
</li>
<li><code>_mlx_batch_centrality</code> (relationship_discovery.py) — <span class="doc-comment-inline">Apply MLX acceleration to centrality scores.</span></li>
<li><code>_classify_path_type</code> (relationship_discovery.py) — <span class="doc-comment-inline">Classify the type of path based on relationships.</span></li>
<li><code>_check_mps_available</code> (document_intelligence.py) — <span class="doc-comment-inline">Check MPS availability lazily - only when actually needed.</span></li>
<li><code>_get_foca_extractor</code> (document_intelligence.py) — <span class="doc-comment-inline">Lazily initialize FOCA metadata extractor (M1-safe async).</span></li>
<li><code>_analyze_ooxml_async</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze OOXML with FOCA metadata enrichment.</span></li>
<li><code>_ela_analysis_cpu_sync</code> (document_intelligence.py) — <span class="doc-comment-inline">Synchronous CPU implementation of ELA.</span></li>
<li><code>restart</code> (document_intelligence.py) — <span class="doc-comment-inline">Restart all stegdetect processes.</span></li>
<li><code>_detect_cycles</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect cycles in flow graph (simplified).</span></li>
<li><code>union</code> (identity_stitching.py)</li>
<li><code>_lexical_similarity</code> (identity_stitching.py) — <span class="doc-comment-inline">Compute lexical similarity based on word overlap.</span></li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>_find_snapshots</code> (archive_discovery.py) — <span class="doc-comment-inline">Find all available snapshots for URL</span></li>
<li><code>_check_search_cache</code> (archive_discovery.py) — <span class="doc-comment-inline">Check search engine cache for URL</span></li>
<li><code>check_bucket_permissions</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check specific permissions on an S3 bucket.</span></li>
<li><code>introspect_endpoint</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Perform full introspection on a GraphQL endpoint.</span></li>
<li><code>enumerate_s3_buckets</code> (exposed_service_hunter.py)
<details><summary>Enumerate S3 buckets for a target.</summary>
<div class="doc-comment">
<p>Enumerate S3 buckets for a target.</p>
<p></p>
<p>Args:</p>
<p>target: Target domain or company name</p>
<p></p>
<p>Returns:</p>
<p>List of exposed S3 buckets</p>
</div>
</details>
</li>
<li><code>query_certificate_transparency</code> (exposed_service_hunter.py)
<details><summary>Query certificate transparency logs.</summary>
<div class="doc-comment">
<p>Query certificate transparency logs.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to query</p>
<p></p>
<p>Returns:</p>
<p>List of discovered subdomains</p>
</div>
</details>
</li>
<li><code>discover_graphql_endpoints</code> (exposed_service_hunter.py)
<details><summary>Discover GraphQL endpoints on a target.</summary>
<div class="doc-comment">
<p>Discover GraphQL endpoints on a target.</p>
<p></p>
<p>Args:</p>
<p>base_url: Base URL to scan</p>
<p></p>
<p>Returns:</p>
<p>List of discovered GraphQL endpoints</p>
</div>
</details>
</li>
<li><code>_detect_layering</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Detect layering pattern (multiple hops to obscure trail).</span></li>
<li><code>_calculate_correlation</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Calculate Pearson correlation coefficient.</span></li>
<li><code>graph_add_domain_ip_relations</code> (network_reconnaissance.py)
<details><summary>FÁZE P9: Add domain→IP relations to GraphManager.</summary>
<div class="doc-comment">
<p>FÁZE P9: Add domain→IP relations to GraphManager.</p>
<p></p>
<p>Streamované přidávání — voláno po každé DNS/A arch resolution.</p>
</div>
</details>
</li>
<li><code>_add_completed_operation</code> (web_intelligence.py)
<details><summary>Add operation to completed_operations with bounded FIFO eviction.</summary>
<div class="doc-comment">
<p>Add operation to completed_operations with bounded FIFO eviction.</p>
<p></p>
<p>Eviction policy: oldest (first-inserted) entries are removed</p>
<p>when the limit is exceeded.</p>
</div>
</details>
</li>
<li><code>_search_source</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Search a single source, return (source_name, results).</span></li>
<li><code>_fetch_archive_today_content</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Fetch content from Archive.today.</span></li>
<li><code>_detect_content_wipes</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect sudden content wipes.</span></li>
<li><code>search_onion_addresses</code> (dark_web_intelligence.py)
<details><summary>Search text for onion addresses.</summary>
<div class="doc-comment">
<p>Search text for onion addresses.</p>
<p></p>
<p>Returns:</p>
<p>List of (address, type) tuples</p>
</div>
</details>
</li>
<li><code>parse_certificate</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Parse X.509 certificate from PEM format.</span></li>
<li><code>_extract_jarm_from_payload</code> (exposure_correlator.py) — <span class="doc-comment-inline">Extract JARM hash from payload_text.</span></li>
<li><code>_classify_jarm_hosting</code> (exposure_correlator.py)
<details><summary>Classify if a JARM + HTTP response indicates hosting vs real content.</summary>
<div class="doc-comment">
<p>Classify if a JARM + HTTP response indicates hosting vs real content.</p>
<p></p>
<p>Returns: "generic_hosting" | "real_content" | "unknown"</p>
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
<li><code>_get_aho_extractor</code> (document_intelligence.py) — <span class="doc-comment-inline">Lazy import of aho_extractor — NOT loaded at document_intelligence boot.</span></li>
<li><code>to_dms</code> (document_intelligence.py) — <span class="doc-comment-inline">Convert decimal degrees to DMS (Degrees, Minutes, Seconds).</span></li>
<li><code>_detect_suspicious_content</code> (document_intelligence.py)
<details><summary>Detect suspicious keywords in text using Aho-Corasick if available.</summary>
<div class="doc-comment">
<p>Detect suspicious keywords in text using Aho-Corasick if available.</p>
<p></p>
<p>Lazy integration (Sprint 8AW): ahocorasick is NOT loaded on boot.</p>
<p>On first call, the automaton is built once and reused.</p>
<p>Falls back to substring scan if aho_extractor is unavailable.</p>
</div>
</details>
</li>
<li><code>_basic_image_analysis</code> (document_intelligence.py) — <span class="doc-comment-inline">Basic analysis without PIL.</span></li>
<li><code>_ensure_processes</code> (document_intelligence.py) — <span class="doc-comment-inline">Ensure worker processes are running (pool instead of single server).</span></li>
<li><code>close</code> (document_intelligence.py) — <span class="doc-comment-inline">Clean up resources: forensics thread pool and stegdetect server.</span></li>
<li><code>_cusum_change</code> (pattern_mining.py) — <span class="doc-comment-inline">CUSUM change detection.</span></li>
<li><code>_detect_periodicity</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect periodic patterns using FFT.</span></li>
<li><code>__init__</code> (identity_stitching.py)</li>
<li><code>_update_profile</code> (identity_stitching.py) — <span class="doc-comment-inline">Update an existing profile (frozen dataclass — uses object.__setattr__).</span></li>
<li><code>get_blockchain_forensics</code> (blockchain_analyzer.py)
<details><summary>Get configured BlockchainForensics instance.</summary>
<div class="doc-comment">
<p>Get configured BlockchainForensics instance.</p>
<p></p>
<p>Args:</p>
<p>etherscan_api_key: Etherscan API key</p>
<p>blockchair_api_key: Blockchair API key</p>
<p></p>
<p>Returns:</p>
<p>BlockchainForensics instance</p>
</div>
</details>
</li>
<li><code>update</code> (academic_search.py) — <span class="doc-comment-inline">Update performance metrics.</span></li>
<li><code>_get_rust_batch_classify</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Lazy load Rust batch_ip_classify, fail-soft if unavailable.</span></li>
<li><code>_extract_tech_spacy</code> (web_intelligence.py) — <span class="doc-comment-inline">Extract tech keywords using spaCy PhraseMatcher.</span></li>
<li><code>fetch_snapshot</code> (temporal_archaeologist.py)</li>
<li><code>initialize</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Initialize the crawler + Rust URL set.</span></li>
<li><code>_get_recommendations</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Get security recommendations.</span></li>
<li><code>_check_takeover_provider</code> (exposure_correlator.py)
<details><summary>Check if CNAME chain matches a takeover-vulnerable provider.</summary>
<div class="doc-comment">
<p>Check if CNAME chain matches a takeover-vulnerable provider.</p>
<p></p>
<p>Returns (provider_name, target_pattern) if matched, None otherwise.</p>
</div>
</details>
</li>
<li><code>_is_generic_hosting_jarm</code> (exposure_correlator.py)
<details><summary>Check if JARM hash indicates generic hosting infrastructure.</summary>
<div class="doc-comment">
<p>Check if JARM hash indicates generic hosting infrastructure.</p>
<p></p>
<p>Generic hosting pages return similar JARM regardless of content.</p>
<p>Real services have distinct fingerprints.</p>
</div>
</details>
</li>
<li><code>get_session_relationship_engine</code> (relationship_discovery.py)
<details><summary>Get or create the module-level session RelationshipDiscoveryEngine singleton.</summary>
<div class="doc-comment">
<p>Get or create the module-level session RelationshipDiscoveryEngine singleton.</p>
<p></p>
<p>Used by GraphService to sync DuckPGQ upserts → NetworkX session graph</p>
<p>for cross-sprint relationship discovery.</p>
</div>
</details>
</li>
<li><code>_get_sparse</code> (relationship_discovery.py) — <span class="doc-comment-inline">Lazy scipy.sparse loader — defers ~144 module load until first use.</span></li>
<li><code>analyze</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze Office document (sync).</span></li>
<li><code>__init__</code> (document_intelligence.py)
<details><summary>Initialize DeepForensicsAnalyzer.</summary>
<div class="doc-comment">
<p>Initialize DeepForensicsAnalyzer.</p>
<p></p>
<p>Args:</p>
<p>orch: Optional orchestrator reference for graph integration (S49-C)</p>
</div>
</details>
</li>
<li><code>_extract_temporal_preferences</code> (pattern_mining.py) — <span class="doc-comment-inline">Extract temporal preferences (preferred hours of activity).</span></li>
<li><code>_correlation_numpy</code> (pattern_mining.py) — <span class="doc-comment-inline">Calculate correlation using NumPy.</span></li>
<li><code>stats</code> (identity_stitching.py) — <span class="doc-comment-inline">Return cache statistics compatible with _BoundedCache API.</span></li>
<li><code>_simple_similarity</code> (identity_stitching.py) — <span class="doc-comment-inline">Simple similarity metric when rapidfuzz is not available.</span></li>
<li><code>__init__</code> (workflow_orchestrator.py)
<details><summary>Initialize workflow orchestrator.</summary>
<div class="doc-comment">
<p>Initialize workflow orchestrator.</p>
<p></p>
<p>Args:</p>
<p>orchestrator: Main orchestrator instance for module access</p>
<p>config: Optional intelligence configuration</p>
</div>
</details>
</li>
<li><code>create_workflow_orchestrator</code> (workflow_orchestrator.py)
<details><summary>Create a configured WorkflowOrchestrator instance.</summary>
<div class="doc-comment">
<p>Create a configured WorkflowOrchestrator instance.</p>
<p></p>
<p>Args:</p>
<p>orchestrator: Main orchestrator instance</p>
<p>config: Optional intelligence configuration</p>
<p></p>
<p>Returns:</p>
<p>Configured WorkflowOrchestrator instance</p>
</div>
</details>
</li>
<li><code>scan_database_ports</code> (exposed_service_hunter.py)
<details><summary>Scan hosts for exposed database ports.</summary>
<div class="doc-comment">
<p>Scan hosts for exposed database ports.</p>
<p></p>
<p>Args:</p>
<p>hosts: List of hostnames or IPs</p>
<p></p>
<p>Returns:</p>
<p>List of exposed database services</p>
</div>
</details>
</li>
<li><code>set</code> (exposed_service_hunter.py)
<details><summary>Set cached value with current timestamp.</summary>
<div class="doc-comment">
<p>Set cached value with current timestamp.</p>
<p></p>
<p>Args:</p>
<p>key: Cache key</p>
<p>value: Value to cache</p>
</div>
</details>
</li>
<li><code>_try_domain_breaker_check</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Fail-soft circuit breaker check. Returns None if breaker unavailable.</span></li>
<li><code>_risk_score_to_level</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Convert risk score to level string.</span></li>
<li><code>_do_search</code> (academic_search.py)</li>
<li><code>_do_search</code> (academic_search.py)</li>
<li><code>_extract_secrets</code> (open_source_collectors.py)</li>
<li><code>_scrape_pastebin_raw</code> (open_source_collectors.py)</li>
<li><code>_scrape_rentry</code> (open_source_collectors.py)</li>
<li><code>parse</code> (open_source_collectors.py)</li>
<li><code>parse</code> (open_source_collectors.py)</li>
<li><code>_parse_date</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Parse WHOIS date string.</span></li>
<li><code>probe_hostname</code> (network_reconnaissance.py)</li>
<li><code>_is_ip_address</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Check if target is IP address.</span></li>
<li><code>bencode_dict</code> (network_reconnaissance.py)</li>
<li><code>_analyze_personal_threats</code> (web_intelligence.py) — <span class="doc-comment-inline">Analyze OSINT data for personal threats.</span></li>
<li><code>_extract_tech_regex</code> (web_intelligence.py) — <span class="doc-comment-inline">Extract tech keywords using word-boundary regex (spaCy fallback).</span></li>
<li><code>_find_shared_attributes</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Find shared attributes between two timelines.</span></li>
<li><code>_get_bitcoin_address_type</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Get Bitcoin address type.</span></li>
<li><code>caesar_decrypt</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Decrypt Caesar cipher with given shift.</span></li>
<li><code>atbash_decrypt</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Decrypt Atbash cipher (reverse alphabet).</span></li>
<li><code>rail_fence_bruteforce</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Try all rail counts from 2 to max_rails.</span></li>
<li><code>_find_vigenere_key_length</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Find Vigenere key length using Index of Coincidence.</span></li>
<li><code>_find_caesar_shift</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Find most likely Caesar shift for text using frequency analysis.</span></li>
<li><code>_calculate_entropy</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Calculate Shannon entropy.</span></li>
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
<li><code>__post_init__</code> (relationship_discovery.py)</li>
<li><code>build_index</code> (relationship_discovery.py) — <span class="doc-comment-inline">Build LSH index from graph.</span></li>
<li><code>enable</code> (relationship_discovery.py) — <span class="doc-comment-inline">Initialize GNN predictor.</span></li>
<li><code>_add_predicted_edge</code> (relationship_discovery.py) — <span class="doc-comment-inline">Add predicted edge to graph.</span></li>
<li><code>clear</code> (relationship_discovery.py) — <span class="doc-comment-inline">Clear all data from the engine.</span></li>
<li><code>analyze_async</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze Office document with FOCA enrichment (async, M1-safe).</span></li>
<li><code>_ela_analysis</code> (document_intelligence.py)
<details><summary>Error Level Analysis - returns manipulation probability 0-1.</summary>
<div class="doc-comment">
<p>Error Level Analysis - returns manipulation probability 0-1.</p>
<p></p>
<p>Uses ProcessPool for CPU-bound analysis to avoid contention with MLX workers.</p>
<p>M1 8GB safe: max 2 workers in shared pool.</p>
</div>
</details>
</li>
<li><code>batch_analyze</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze multiple documents (sync wrapper for backward compatibility).</span></li>
<li><code>_cosine_similarity</code> (document_intelligence.py) — <span class="doc-comment-inline">Compute cosine similarity between two vectors.</span></li>
<li><code>_extract_keywords</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract high-value keywords from text.</span></li>
<li><code>clear</code> (identity_stitching.py) — <span class="doc-comment-inline">Clear all data from the engine.</span></li>
<li><code>optimize_memory</code> (identity_stitching.py) — <span class="doc-comment-inline">Optimize memory usage by clearing caches and forcing GC.</span></li>
<li><code>get_timeline</code> (archive_discovery.py) — <span class="doc-comment-inline">Get timeline of changes for a URL.</span></li>
<li><code>_extract_from_snapshots</code> (archive_discovery.py) — <span class="doc-comment-inline">Extract content from snapshots concurrently</span></li>
<li><code>serialize</code> (workflow_orchestrator.py)</li>
<li><code>_count_anomalies</code> (workflow_orchestrator.py) — <span class="doc-comment-inline">Count simple anomalies in findings.</span></li>
<li><code>check_bucket</code> (exposed_service_hunter.py)</li>
<li><code>check_port</code> (exposed_service_hunter.py)</li>
<li><code>check_endpoint</code> (exposed_service_hunter.py)</li>
<li><code>check_host</code> (exposed_service_hunter.py)</li>
<li><code>check_host</code> (exposed_service_hunter.py)</li>
<li><code>_get_circuit_breaker_module</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Lazily import circuit_breaker to avoid import-time session creation.</span></li>
<li><code>_parse_transaction</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Parse raw transaction data into Transaction object.</span></li>
<li><code>_init_sources</code> (academic_search.py) — <span class="doc-comment-inline">Initialize source adapters.</span></li>
<li><code>_check_admission</code> (open_source_collectors.py) — <span class="doc-comment-inline">Check M1ResourceGovernor admission. Returns True if allowed.</span></li>
<li><code>probe_known_hashes</code> (network_reconnaissance.py)
<details><summary>Dotazovat DHT pro known malware info_hashes z MalwareBazaar.</summary>
<div class="doc-comment">
<p>Dotazovat DHT pro known malware info_hashes z MalwareBazaar.</p>
<p>Vrátí [(info_hash, status)].</p>
</div>
</details>
</li>
<li><code>_calculate_threat_score</code> (web_intelligence.py) — <span class="doc-comment-inline">Calculate overall threat score.</span></li>
<li><code>_score_to_threat_level</code> (web_intelligence.py) — <span class="doc-comment-inline">Convert threat score to threat level.</span></li>
<li><code>get_operation_results</code> (web_intelligence.py) — <span class="doc-comment-inline">Get comprehensive operation results.</span></li>
<li><code>_detect_disappearances</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect content disappearances.</span></li>
<li><code>_calculate_correlation_score</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Calculate correlation score between two timelines.</span></li>
<li><code>_find_temporal_proximity</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Find events that are temporally close.</span></li>
<li><code>analyze_bitcoin_address</code> (dark_web_intelligence.py)
<details><summary>Analyze Bitcoin address.</summary>
<div class="doc-comment">
<p>Analyze Bitcoin address.</p>
<p></p>
<p>Note: Without external APIs, we can only do basic validation.</p>
<p>For full analysis, would need blockchain.info or similar API.</p>
</div>
</details>
</li>
<li><code>cluster_addresses</code> (dark_web_intelligence.py)
<details><summary>Cluster addresses that might belong to the same entity.</summary>
<div class="doc-comment">
<p>Cluster addresses that might belong to the same entity.</p>
<p></p>
<p>Uses heuristics like:</p>
<p>- Common input ownership</p>
<p>- Change address patterns</p>
</div>
</details>
</li>
<li><code>_nvd_to_cve</code> (exposure_clients.py) — <span class="doc-comment-inline">Convert NVD vulnerability format to our CVE dict.</span></li>
<li><code>_index_of_coincidence</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Calculate Index of Coincidence (0.067 for English, 0.0385 for random).</span></li>
<li><code>parse_certificate_der</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Parse certificate from DER format.</span></li>
<li><code>_make_open_bucket_finding</code> (exposure_correlator.py) — <span class="doc-comment-inline">Produce an open_bucket finding from an asset with bucket signal.</span></li>
<li><code>correlate</code> (exposure_correlator.py)
<details><summary>Correlate exposure signals from findings.</summary>
<div class="doc-comment">
<p>Correlate exposure signals from findings.</p>
<p></p>
<p>Returns:</p>
<p>List of CanonicalFinding (source_type="exposure_correlation").</p>
</div>
</details>
</li>
<li><code>_make_pair</code> (relationship_discovery.py)
<details><summary>Create a deterministic (sorted) pair tuple.</summary>
<div class="doc-comment">
<p>Create a deterministic (sorted) pair tuple.</p>
<p></p>
<p>Replaces tuple(sorted([a, b])) which allocates a temporary list.</p>
<p>O(1) comparison vs O(n log n) sort, but for n=2 the main benefit</p>
<p>is avoiding allocation overhead in tight loops.</p>
</div>
</details>
</li>
<li><code>_find_relationship</code> (relationship_discovery.py) — <span class="doc-comment-inline">Find relationship between two entities.</span></li>
<li><code>optimize_memory</code> (relationship_discovery.py) — <span class="doc-comment-inline">Optimize memory usage by clearing caches and forcing GC.</span></li>
<li><code>close</code> (document_intelligence.py) — <span class="doc-comment-inline">Close FOCA extractor and release resources (fail-safe).</span></li>
<li><code>_extract_comments_from_xml</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract comments from Word XML.</span></li>
<li><code>__init__</code> (document_intelligence.py)</li>
<li><code>_create_unknown_analysis</code> (document_intelligence.py) — <span class="doc-comment-inline">Create analysis for unknown file type.</span></li>
<li><code>_check_mlx</code> (document_intelligence.py) — <span class="doc-comment-inline">Check if MLX is available.</span></li>
<li><code>search_one</code> (document_intelligence.py)</li>
<li><code>_ewma_drift</code> (pattern_mining.py) — <span class="doc-comment-inline">EWMA-based drift detection.</span></li>
<li><code>_parse_search_results</code> (archive_discovery.py) — <span class="doc-comment-inline">Parse Archive.today search results.</span></li>
<li><code>_classify_signal_quality</code> (workflow_orchestrator.py) — <span class="doc-comment-inline">Classify signal as strong/mixed/weak for scheduler filtering.</span></li>
<li><code>_fetch_bitcoin_transaction_detail</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Fetch detailed Bitcoin transaction.</span></li>
<li><code>_detect_rapid_trading</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Detect rapid trading pattern.</span></li>
<li><code>_normalize_url</code> (academic_search.py) — <span class="doc-comment-inline">Normalize URL for deduplication.</span></li>
<li><code>_paste_cache_put</code> (open_source_collectors.py) — <span class="doc-comment-inline">Bounded insert. On overflow, evicts oldest 10% (FIFO).</span></li>
<li><code>_scrape_one</code> (open_source_collectors.py)</li>
<li><code>_get_governor</code> (open_source_collectors.py) — <span class="doc-comment-inline">Lazy load governor singleton to avoid circular imports and ensure consistent state.</span></li>
<li><code>check_perm</code> (network_reconnaissance.py)</li>
<li><code>reverse_lookup</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Perform reverse DNS lookup.</span></li>
<li><code>_extract_list</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Extract list field from WHOIS.</span></li>
<li><code>resolve_domain</code> (network_reconnaissance.py) — <span class="doc-comment-inline">A-record lookup — returns list of IPv4 addresses.</span></li>
<li><code>reverse_lookup</code> (network_reconnaissance.py) — <span class="doc-comment-inline">PTR record lookup — returns list of hostnames.</span></li>
<li><code>_init_metrics_and_config</code> (web_intelligence.py) — <span class="doc-comment-inline">Initialize metrics and configuration from config dict.</span></li>
<li><code>_extract_hiring_patterns</code> (web_intelligence.py) — <span class="doc-comment-inline">Detect hiring patterns in job posting text.</span></li>
<li><code>_extract_pain_points</code> (web_intelligence.py) — <span class="doc-comment-inline">Detect inferred pain points from job posting text.</span></li>
<li><code>_detect_identity_changes</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect identity changes in snapshots.</span></li>
<li><code>_index_of_coincidence</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Calculate Index of Coincidence.</span></li>
<li><code>_chi_square_test</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Perform chi-square test against uniform distribution.</span></li>
<li><code>_is_likely_encrypted</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Determine if data is likely encrypted.</span></li>
<li><code>_normalize_host</code> (exposure_correlator.py) — <span class="doc-comment-inline">Strip port, scheme, and normalize to lowercase.</span></li>
<li><code>_make_cert_domain_finding</code> (exposure_correlator.py) — <span class="doc-comment-inline">Produce a cert_domain_relation finding.</span></li>
<li><code>paper_id</code> (academic_discovery.py) — <span class="doc-comment-inline">Paper ID usable with get_citations — prefer DOI, fallback to title-hash.</span></li>
<li><code>_invalidate_caches</code> (relationship_discovery.py) — <span class="doc-comment-inline">Invalidate all cached computations.</span></li>
<li><code>create_relationship_engine</code> (relationship_discovery.py)
<details><summary>Factory function to create a RelationshipDiscoveryEngine.</summary>
<div class="doc-comment">
<p>Factory function to create a RelationshipDiscoveryEngine.</p>
<p></p>
<p>Note:</p>
<p>max_memory_mb=512 is the recommended ceiling for M1 8GB UMA.</p>
<p>The parameter is advisory — not hard-enforced.</p>
</div>
</details>
</li>
<li><code>_parse_exif_datetime</code> (document_intelligence.py) — <span class="doc-comment-inline">Parse EXIF datetime string.</span></li>
<li><code>convert_dms</code> (document_intelligence.py) — <span class="doc-comment-inline">Convert DMS tuple to decimal degrees.</span></li>
<li><code>_estimate_optimal_chunk_size</code> (document_intelligence.py)
<details><summary>Estimate optimal chunk size based on available RAM.</summary>
<div class="doc-comment">
<p>Estimate optimal chunk size based on available RAM.</p>
<p></p>
<p>M1 8GB optimization: Target &lt; 5.5GB to leave room for system</p>
</div>
</details>
</li>
<li><code>_gini_coefficient</code> (pattern_mining.py) — <span class="doc-comment-inline">Calculate Gini coefficient for concentration.</span></li>
<li><code>_invalidate_caches</code> (identity_stitching.py) — <span class="doc-comment-inline">Invalidate all cached computations.</span></li>
<li><code>_extract_tweet_id</code> (archive_discovery.py) — <span class="doc-comment-inline">Extract tweet ID from Twitter/X URL</span></li>
<li><code>resurrect_url</code> (archive_discovery.py) — <span class="doc-comment-inline">Quick resurrect URL and return content.</span></li>
<li><code>_add_timeline_event</code> (workflow_orchestrator.py)
<details><summary>Add event to execution timeline.</summary>
<div class="doc-comment">
<p>Add event to execution timeline.</p>
<p></p>
<p>Args:</p>
<p>event_type: Type of event</p>
<p>details: Event details</p>
</div>
</details>
</li>
<li><code>register_module</code> (workflow_orchestrator.py)
<details><summary>Register a module instance.</summary>
<div class="doc-comment">
<p>Register a module instance.</p>
<p></p>
<p>Args:</p>
<p>name: Module name</p>
<p>instance: Module instance</p>
</div>
</details>
</li>
<li><code>__aenter__</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Async context manager entry.</span></li>
<li><code>_fetch_ethereum_transactions</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Fetch Ethereum transactions from Etherscan.</span></li>
<li><code>_scrape_one</code> (open_source_collectors.py)</li>
<li><code>_scrape_one</code> (open_source_collectors.py)</li>
<li><code>resolve_aaaa</code> (network_reconnaissance.py) — <span class="doc-comment-inline">AAAA-record lookup — returns list of IPv6 addresses.</span></li>
<li><code>_analyze_web_vulnerabilities</code> (web_intelligence.py) — <span class="doc-comment-inline">Analyze web data for vulnerabilities.</span></li>
<li><code>_estimate_block_size</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Estimate block cipher block size using Kasiski-like method.</span></li>
<li><code>crack_classical_cipher</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Automatically crack classical cipher.</span></li>
<li><code>generate_password_hash</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Generate password hash.</span></li>
<li><code>_async_candidate_gen</code> (exposure_correlator.py) — <span class="doc-comment-inline">Async generator that yields from an iterator with a cap.</span></li>
<li><code>_get_nx</code> (relationship_discovery.py) — <span class="doc-comment-inline">Lazy networkx importer — imported only when first graph method is called.</span></li>
<li><code>_analyze_ole</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze legacy OLE format.</span></li>
<li><code>__init__</code> (document_intelligence.py)</li>
<li><code>analyze_one</code> (document_intelligence.py)</li>
<li><code>_get_pywt</code> (pattern_mining.py) — <span class="doc-comment-inline">Lazy import pywt.</span></li>
<li><code>update</code> (pattern_mining.py) — <span class="doc-comment-inline">Update statistics with new value.</span></li>
<li><code>_get_nx</code> (identity_stitching.py) — <span class="doc-comment-inline">Lazy networkx importer — imported only when first graph method is called.</span></li>
<li><code>_get_ig</code> (identity_stitching.py) — <span class="doc-comment-inline">Lazy igraph importer — M1-optimized C-core, preferred over networkx.</span></li>
<li><code>__post_init__</code> (identity_stitching.py)</li>
<li><code>_register_profile_lsh</code> (identity_stitching.py)
<details><summary>Register profile fingerprint in LSH index. Call ONLY on first add.</summary>
<div class="doc-comment">
<p>Register profile fingerprint in LSH index. Call ONLY on first add.</p>
<p>LSH has no remove() — calling this on update would duplicate entries.</p>
</div>
</details>
</li>
<li><code>_is_error_page</code> (archive_discovery.py) — <span class="doc-comment-inline">Check if content is an error page</span></li>
<li><code>snapshots_one_shot</code> (archive_discovery.py)
<details><summary>One-shot CDX lookup — vytvoří a zavře vlastní session.</summary>
<div class="doc-comment">
<p>One-shot CDX lookup — vytvoří a zavře vlastní session.</p>
<p>USE CASE: compat layer, tests, ad-hoc volání bez externího session.</p>
<p>PRO: žádné unclosed session warnings.</p>
</div>
</details>
</li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>_get_client</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Get or create HTTP client.</span></li>
<li><code>_is_valid_address</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Validate address format for given chain.</span></li>
<li><code>_fetch_transactions</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Fetch raw transactions for an address.</span></li>
<li><code>make_dedup_item</code> (academic_search.py) — <span class="doc-comment-inline">Build DedupItem from SearchResult (CPU-bound hashlib).</span></li>
<li><code>_host_semaphore</code> (open_source_collectors.py) — <span class="doc-comment-inline">Lazy per-host Semaphore creation (M1 soft cap 3 concurrent fetches/host).</span></li>
<li><code>_extract_field</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Extract single field from WHOIS.</span></li>
<li><code>_init_per_host_gate</code> (web_intelligence.py)
<details><summary>ISSUE #15 FIX: Per-host concurrency gate — prevents head-of-line blocking</summary>
<div class="doc-comment">
<p>ISSUE #15 FIX: Per-host concurrency gate — prevents head-of-line blocking</p>
<p>when multiple operations target the same host (e.g. example.com scraping).</p>
<p>BoundedPerHostGate uses LRU eviction at 512 hosts × 4 concurrent = ~128 KB RAM.</p>
</div>
</details>
</li>
<li><code>_track_task</code> (web_intelligence.py) — <span class="doc-comment-inline">Register an owned operation task. Silently drops if at capacity.</span></li>
<li><code>_calculate_timeline_confidence</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Calculate confidence score for timeline.</span></li>
<li><code>correlate</code> (passive_fingerprint.py)
<details><summary>Correlate fingerprints from findings.</summary>
<div class="doc-comment">
<p>Correlate fingerprints from findings.</p>
<p></p>
<p>Returns list of CanonicalFinding with source_type="passive_fingerprint".</p>
</div>
</details>
</li>
<li><code>_bounded_insert_content_cache</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Insert into content_cache with FIFO LRU eviction at limit.</span></li>
<li><code>_bounded_insert_visited_url</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Insert into visited_urls with FIFO LRU eviction at limit.</span></li>
<li><code>_bounded_insert_discovered_service</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Insert into discovered_services with FIFO eviction at limit.</span></li>
<li><code>_validate_bitcoin_address</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Basic Bitcoin address validation.</span></li>
<li><code>close</code> (exposure_clients.py)</li>
<li><code>_osv_severity</code> (exposure_clients.py) — <span class="doc-comment-inline">Extract severity from OSV format.</span></li>
<li><code>_is_base64</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Check if text is valid base64.</span></li>
<li><code>_detect_language</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Detect language of text.</span></li>
<li><code>_normalize_url</code> (exposure_correlator.py) — <span class="doc-comment-inline">Normalize bucket URL to base key.</span></li>
<li><code>_get_csr_matrix</code> (relationship_discovery.py) — <span class="doc-comment-inline">Lazy csr_matrix loader.</span></li>
<li><code>_get_lil_matrix</code> (relationship_discovery.py) — <span class="doc-comment-inline">Lazy lil_matrix loader.</span></li>
<li><code>__post_init__</code> (relationship_discovery.py)</li>
<li><code>_node_to_minhash</code> (relationship_discovery.py) — <span class="doc-comment-inline">Create MinHash from node's neighbors.</span></li>
<li><code>get_memory_usage</code> (relationship_discovery.py) — <span class="doc-comment-inline">Estimate memory usage of key data structures.</span></li>
<li><code>decimal_to_dms</code> (document_intelligence.py)</li>
<li><code>__del__</code> (document_intelligence.py) — <span class="doc-comment-inline">Fallback shutdown on garbage collection.</span></li>
<li><code>add_username</code> (identity_stitching.py) — <span class="doc-comment-inline">Add a username entry for a platform.</span></li>
<li><code>get_username</code> (identity_stitching.py) — <span class="doc-comment-inline">Get username for a specific platform.</span></li>
<li><code>get_memory_usage</code> (identity_stitching.py) — <span class="doc-comment-inline">Estimate memory usage of key data structures.</span></li>
<li><code>datetime</code> (archive_discovery.py) — <span class="doc-comment-inline">Parse timestamp as datetime.</span></li>
<li><code>__aenter__</code> (archive_discovery.py)</li>
<li><code>get_archive_resurrector</code> (archive_discovery.py) — <span class="doc-comment-inline">Get or create global ArchiveResurrector instance</span></li>
<li><code>_extract_domain</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Extract domain from URL for circuit breaker check.</span></li>
<li><code>score</code> (academic_search.py) — <span class="doc-comment-inline">Calculate overall source score.</span></li>
<li><code>make_dedup_item</code> (academic_search.py)</li>
<li><code>_get_paste_rate_lock</code> (open_source_collectors.py) — <span class="doc-comment-inline">ISSUE-014 FIX: Lazily create paste rate lock in the current event loop.</span></li>
<li><code>get_open_source_collectors</code> (open_source_collectors.py) — <span class="doc-comment-inline">Get the canonical OpenSourceCollectors singleton.</span></li>
<li><code>__init__</code> (network_reconnaissance.py)</li>
<li><code>_extract_email</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Extract email field, handling privacy protection.</span></li>
<li><code>__init__</code> (network_reconnaissance.py)</li>
<li><code>_analyze_personal_vulnerabilities</code> (web_intelligence.py) — <span class="doc-comment-inline">Analyze OSINT data for personal vulnerabilities.</span></li>
<li><code>get_operation_status</code> (web_intelligence.py) — <span class="doc-comment-inline">Get status of a specific operation.</span></li>
<li><code>correlate</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Correlate tech-stack signals from findings.</span></li>
<li><code>__init__</code> (dark_web_intelligence.py)</li>
<li><code>_mark_url_visited</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Mark URL as visited (Rust MmapUrlSet or fallback OrderedDict).</span></li>
<li><code>_aclose_stream</code> (exposure_clients.py) — <span class="doc-comment-inline">P15: Close aiohttp AsyncBufferedReader on early break.</span></li>
<li><code>_open_env</code> (exposure_clients.py) — <span class="doc-comment-inline">Otevře LMDB env lazy.</span></li>
<li><code>__init__</code> (cryptographic_intelligence.py)</li>
<li><code>parse_certificate</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Parse X.509 certificate.</span></li>
<li><code>_get_academic_search_engine</code> (academic_discovery.py) — <span class="doc-comment-inline">Lazy-load AcademicSearchEngine from canonical path.</span></li>
<li><code>search_arxiv_sync</code> (academic_discovery.py)
<details><summary>Synchronous wrapper for search_arxiv.</summary>
<div class="doc-comment">
<p>Synchronous wrapper for search_arxiv.</p>
<p></p>
<p>Deprecated for async callers: use `await search_arxiv(...)` inside an event loop.</p>
</div>
</details>
</li>
<li><code>search_crossref_sync</code> (academic_discovery.py)
<details><summary>Synchronous wrapper for search_crossref.</summary>
<div class="doc-comment">
<p>Synchronous wrapper for search_crossref.</p>
<p></p>
<p>Deprecated for async callers: use `await search_crossref(...)` inside an event loop.</p>
</div>
</details>
</li>
<li><code>search_semantic_scholar_sync</code> (academic_discovery.py)
<details><summary>Synchronous wrapper for search_semantic_scholar.</summary>
<div class="doc-comment">
<p>Synchronous wrapper for search_semantic_scholar.</p>
<p></p>
<p>Deprecated for async callers: use `await search_semantic_scholar(...)` inside an event loop.</p>
</div>
</details>
</li>
<li><code>get_top_pairs</code> (relationship_discovery.py) — <span class="doc-comment-inline">Get top N entity pairs by affinity score.</span></li>
<li><code>__init__</code> (relationship_discovery.py)</li>
<li><code>get_candidates</code> (relationship_discovery.py) — <span class="doc-comment-inline">Return candidate nodes for prediction (≤1% of total).</span></li>
<li><code>__init__</code> (relationship_discovery.py)</li>
<li><code>get_source_credibility</code> (relationship_discovery.py) — <span class="doc-comment-inline">Get credibility score for source from bandit.</span></li>
<li><code>_ela_analysis_mps</code> (document_intelligence.py) — <span class="doc-comment-inline">MPS-accelerated ELA analysis (runs sync MPS in ProcessPool to avoid GIL).</span></li>
<li><code>_ela_analysis_cpu</code> (document_intelligence.py) — <span class="doc-comment-inline">CPU-based ELA analysis (runs in ProcessPool to avoid blocking MLX workers).</span></li>
<li><code>__init__</code> (pattern_mining.py)</li>
<li><code>get_top_k</code> (pattern_mining.py) — <span class="doc-comment-inline">Get top k most frequent items using heapq for O(n log k) performance (Sprint 26).</span></li>
<li><code>find</code> (identity_stitching.py)</li>
<li><code>groups</code> (identity_stitching.py)</li>
<li><code>put</code> (identity_stitching.py) — <span class="doc-comment-inline">Put item. Evicts on TTL expiry or memory pressure.</span></li>
<li><code>__post_init__</code> (identity_stitching.py)</li>
<li><code>_normalize_username</code> (identity_stitching.py) — <span class="doc-comment-inline">Normalize username for comparison.</span></li>
<li><code>_extract_title</code> (archive_discovery.py) — <span class="doc-comment-inline">Extract title from HTML.</span></li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>cleanup</code> (archive_discovery.py) — <span class="doc-comment-inline">Cleanup resources</span></li>
<li><code>_throttle</code> (archive_discovery.py)</li>
<li><code>_throttle</code> (archive_discovery.py)</li>
<li><code>_throttle</code> (archive_discovery.py)</li>
<li><code>_has_infra_hints</code> (workflow_orchestrator.py) — <span class="doc-comment-inline">Check if finding has infrastructure-related hints.</span></li>
<li><code>__aexit__</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Async context manager exit.</span></li>
<li><code>__exit__</code> (exposed_service_hunter.py)</li>
<li><code>__del__</code> (exposed_service_hunter.py)</li>
<li><code>check_s3_bucket</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if a specific S3 bucket exists and is exposed.</span></li>
<li><code>scan_graphql_endpoint</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Scan a specific GraphQL endpoint.</span></li>
<li><code>_generate_cluster_id</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Generate a unique cluster ID from addresses.</span></li>
<li><code>_is_likely_contract</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Heuristic: check if address is likely a contract.</span></li>
<li><code>close</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Close HTTP client and cleanup resources.</span></li>
<li><code>_extract_key_terms</code> (academic_search.py) — <span class="doc-comment-inline">Extract key terms from query.</span></li>
<li><code>cleanup</code> (academic_search.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>_throttle</code> (academic_search.py)</li>
<li><code>search_pastebin</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search paste sites for secrets/leaks.</span></li>
<li><code>search_usenet</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search Usenet archives.</span></li>
<li><code>search_matrix</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search public Matrix rooms.</span></li>
<li><code>search_academic</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search academic preprint servers.</span></li>
<li><code>search_sec_edgar</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search SEC EDGAR filings.</span></li>
<li><code>search_court_records</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search federal court cases.</span></li>
<li><code>gather_pastebin</code> (open_source_collectors.py)</li>
<li><code>gather_usenet</code> (open_source_collectors.py)</li>
<li><code>gather_matrix</code> (open_source_collectors.py)</li>
<li><code>gather_academic</code> (open_source_collectors.py)</li>
<li><code>gather_sec_edgar</code> (open_source_collectors.py)</li>
<li><code>gather_court_records</code> (open_source_collectors.py)</li>
<li><code>_recon_ip</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Reconnaissance for IP address.</span></li>
<li><code>__init__</code> (network_reconnaissance.py)</li>
<li><code>_get_init_lock</code> (web_intelligence.py) — <span class="doc-comment-inline">ISSUE-014 FIX: Lazily create init lock in the current event loop.</span></li>
<li><code>_update_success_rate</code> (web_intelligence.py) — <span class="doc-comment-inline">Update operation success rate.</span></li>
<li><code>_content_similarity</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Calculate similarity between two content strings.</span></li>
<li><code>_get_bfs_lock</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">ISSUE-014 FIX: Lazily create BFS lock in the current event loop.</span></li>
<li><code>_is_url_visited</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Check if URL was visited (Rust MmapUrlSet or fallback OrderedDict).</span></li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>_throttle</code> (exposure_clients.py)</li>
<li><code>_throttle</code> (exposure_clients.py)</li>
<li><code>_throttle</code> (exposure_clients.py)</li>
<li><code>analyze_hash</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Analyze hash value.</span></li>
<li><code>__eq__</code> (relationship_discovery.py)</li>
<li><code>analyze_one</code> (document_intelligence.py)</li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>__init__</code> (pattern_mining.py)</li>
<li><code>__init__</code> (identity_stitching.py)</li>
<li><code>get</code> (identity_stitching.py) — <span class="doc-comment-inline">Get item. Returns None on miss or expired.</span></li>
<li><code>_extract_email_domain</code> (identity_stitching.py) — <span class="doc-comment-inline">Extract domain from email address.</span></li>
<li><code>_extract_words</code> (identity_stitching.py) — <span class="doc-comment-inline">Extract words from text.</span></li>
<li><code>export_matches</code> (identity_stitching.py) — <span class="doc-comment-inline">Export all matches as list of dictionaries.</span></li>
<li><code>export_stitched</code> (identity_stitching.py) — <span class="doc-comment-inline">Export stitched identities as list of dictionaries.</span></li>
<li><code>__post_init__</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (archive_discovery.py)</li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (archive_discovery.py)</li>
<li><code>_select_best_content</code> (archive_discovery.py) — <span class="doc-comment-inline">Select best content from results</span></li>
<li><code>search_archives</code> (archive_discovery.py) — <span class="doc-comment-inline">Search for archived versions of a URL.</span></li>
<li><code>get_wayback_snapshots</code> (archive_discovery.py) — <span class="doc-comment-inline">Get Wayback Machine snapshots for a URL.</span></li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (archive_discovery.py)</li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aenter__</code> (exposed_service_hunter.py)</li>
<li><code>__aexit__</code> (exposed_service_hunter.py)</li>
<li><code>__aenter__</code> (exposed_service_hunter.py)</li>
<li><code>__aexit__</code> (exposed_service_hunter.py)</li>
<li><code>__aenter__</code> (exposed_service_hunter.py)</li>
<li><code>__aexit__</code> (exposed_service_hunter.py)</li>
<li><code>__aenter__</code> (exposed_service_hunter.py)</li>
<li><code>__aexit__</code> (exposed_service_hunter.py)</li>
<li><code>clear</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Clear all cached entries.</span></li>
<li><code>quick_hunt</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Quick exposed service hunt.</span></li>
<li><code>_analyze_generic_wallet</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Generic wallet analysis when specific API unavailable.</span></li>
<li><code>__post_init__</code> (academic_search.py)</li>
<li><code>__post_init__</code> (academic_search.py)</li>
<li><code>success_rate</code> (academic_search.py)</li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>_mask_secret</code> (open_source_collectors.py)</li>
<li><code>_parse_whois</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Parse raw WHOIS data into structured format.</span></li>
<li><code>_analyze_security_indicators</code> (web_intelligence.py) — <span class="doc-comment-inline">Analyze web data for security indicators.</span></li>
<li><code>create_unified_intelligence</code> (web_intelligence.py) — <span class="doc-comment-inline">Factory function to create unified intelligence system.</span></li>
<li><code>lifespan_days</code> (temporal_archaeologist.py)</li>
<li><code>__aenter__</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Async context manager entry.</span></li>
<li><code>_recover_from_common_crawl</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Common Crawl index.</span></li>
<li><code>_detect_sudden_changes</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect sudden changes in metadata or content.</span></li>
<li><code>clear_cache</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Clear internal cache.</span></li>
<li><code>recover_deleted_content</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Quick function to recover deleted content.</span></li>
<li><code>reconstruct_timeline</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Quick function to reconstruct timeline.</span></li>
<li><code>detect_anomalies</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Quick function to detect anomalies.</span></li>
<li><code>reset_fingerprint_stats</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Reset all stats to zero (for probe test isolation).</span></li>
<li><code>close</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Close Tor connections.</span></li>
<li><code>__aenter__</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Async context manager entry - initializes Tor connection.</span></li>
<li><code>close</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Close crawler and cleanup session state.</span></li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>_get_session</code> (exposure_clients.py)</li>
<li><code>_get_session</code> (exposure_clients.py)</li>
<li><code>_osv_affected</code> (exposure_clients.py) — <span class="doc-comment-inline">Extract affected packages from OSV format.</span></li>
<li><code>_get_hash_function</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Get Python hash function for type.</span></li>
<li><code>reset_correlator_stats</code> (exposure_correlator.py) — <span class="doc-comment-inline">Reset all stats to zero (for probe test isolation).</span></li>
<li><code>_make_suspicious_fp_finding</code> (exposure_correlator.py) — <span class="doc-comment-inline">Produce a suspicious_service_fingerprint finding.</span></li>
<li><code>reset</code> (exposure_correlator.py) — <span class="doc-comment-inline">Reset internal state and stats.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert entity to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert relationship to dictionary.</span></li>
<li><code>__post_init__</code> (relationship_discovery.py)</li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert path to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert community to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert affinity matrix to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert communication to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert document to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert influence model to dictionary.</span></li>
<li><code>add_document</code> (relationship_discovery.py) — <span class="doc-comment-inline">S49-E: Track URL to node mapping for quick lookup.</span></li>
<li><code>get_entity</code> (relationship_discovery.py) — <span class="doc-comment-inline">Get an entity by ID.</span></li>
<li><code>export_graph</code> (relationship_discovery.py) — <span class="doc-comment-inline">Export the relationship graph as NetworkX graph.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Export engine state as dictionary.</span></li>
<li><code>get_stats</code> (relationship_discovery.py) — <span class="doc-comment-inline">Get engine statistics.</span></li>
<li><code>to_google_maps_url</code> (document_intelligence.py) — <span class="doc-comment-inline">Generate Google Maps URL.</span></li>
<li><code>__init__</code> (document_intelligence.py)</li>
<li><code>_stegdetect</code> (document_intelligence.py) — <span class="doc-comment-inline">Run stegdetect on image using persistent server.</span></li>
<li><code>ensure_running</code> (document_intelligence.py) — <span class="doc-comment-inline">Alias for _ensure_processes (Sprint 45 compatibility).</span></li>
<li><code>_run_forensics_async</code> (document_intelligence.py) — <span class="doc-comment-inline">Run async forensics analysis in a separate thread with its own event loop.</span></li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>get_frequency</code> (pattern_mining.py) — <span class="doc-comment-inline">Get frequency of item in current window.</span></li>
<li><code>_normalize_key</code> (identity_stitching.py) — <span class="doc-comment-inline">Normalize key so (A,B) and (B,A) map to same slot.</span></li>
<li><code>clear</code> (identity_stitching.py) — <span class="doc-comment-inline">Clear all entries.</span></li>
<li><code>to_dict</code> (identity_stitching.py) — <span class="doc-comment-inline">Convert to dictionary.</span></li>
<li><code>__post_init__</code> (identity_stitching.py)</li>
<li><code>get_all_usernames</code> (identity_stitching.py) — <span class="doc-comment-inline">Get all usernames across platforms.</span></li>
<li><code>get_platforms</code> (identity_stitching.py) — <span class="doc-comment-inline">Get set of platforms where this identity appears.</span></li>
<li><code>to_dict</code> (identity_stitching.py) — <span class="doc-comment-inline">Convert profile to dictionary.</span></li>
<li><code>to_dict</code> (identity_stitching.py) — <span class="doc-comment-inline">Convert match to dictionary.</span></li>
<li><code>to_dict</code> (identity_stitching.py) — <span class="doc-comment-inline">Convert stitched identity to dictionary.</span></li>
<li><code>get_profile</code> (identity_stitching.py) — <span class="doc-comment-inline">Get a profile by ID.</span></li>
<li><code>_normalize_email</code> (identity_stitching.py) — <span class="doc-comment-inline">Normalize email for comparison.</span></li>
<li><code>_normalize_text</code> (identity_stitching.py) — <span class="doc-comment-inline">Normalize text for comparison.</span></li>
<li><code>to_dict</code> (identity_stitching.py) — <span class="doc-comment-inline">Export engine state as dictionary.</span></li>
<li><code>get_stats</code> (identity_stitching.py) — <span class="doc-comment-inline">Get engine statistics.</span></li>
<li><code>create_identity_stitching_engine</code> (identity_stitching.py) — <span class="doc-comment-inline">Factory function to create an IdentityStitchingEngine.</span></li>
<li><code>wayback_url</code> (archive_discovery.py) — <span class="doc-comment-inline">Get Wayback Machine URL for this snapshot.</span></li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aenter__</code> (archive_discovery.py)</li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aenter__</code> (archive_discovery.py)</li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aenter__</code> (archive_discovery.py)</li>
<li><code>extract_with_limit</code> (archive_discovery.py)</li>
<li><code>get_statistics</code> (archive_discovery.py) — <span class="doc-comment-inline">Get resurrector statistics</span></li>
<li><code>__aenter__</code> (archive_discovery.py)</li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>_run_with_timeout</code> (workflow_orchestrator.py)</li>
<li><code>to_dict</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Convert to dictionary.</span></li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>get_statistics</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Get hunter statistics.</span></li>
<li><code>close</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Close database connection.</span></li>
<li><code>_is_likely_exchange</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Heuristic: check if address is likely an exchange.</span></li>
<li><code>__aenter__</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Async context manager entry.</span></li>
<li><code>__aexit__</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Async context manager exit.</span></li>
<li><code>search</code> (academic_search.py) — <span class="doc-comment-inline">Search the source with the given query.</span></li>
<li><code>get_performance</code> (academic_search.py) — <span class="doc-comment-inline">Get performance metrics for this source.</span></li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>_analyze_query</code> (academic_search.py) — <span class="doc-comment-inline">Analyze the query for optimization.</span></li>
<li><code>search_with_limit</code> (academic_search.py)</li>
<li><code>get_source_performance</code> (academic_search.py) — <span class="doc-comment-inline">Get performance metrics for all sources.</span></li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>cleanup</code> (academic_search.py) — <span class="doc-comment-inline">Cleanup resources (placeholder for future connection/state cleanup).</span></li>
<li><code>build_url</code> (open_source_collectors.py) — <span class="doc-comment-inline">Return one URL or an ordered list of fallback URLs to try.</span></li>
<li><code>parse</code> (open_source_collectors.py) — <span class="doc-comment-inline">Parse the response body. Return None on parse error or empty body.</span></li>
<li><code>close</code> (open_source_collectors.py) — <span class="doc-comment-inline">Graceful shutdown — no-op since sessions are shared singletons.</span></li>
<li><code>close</code> (network_reconnaissance.py) — <span class="doc-comment-inline">No-op — kept for API consistency.</span></li>
<li><code>is_degraded</code> (web_intelligence.py) — <span class="doc-comment-inline">True pokud modul běží v degraded mode (chybí volitelné komponenty).</span></li>
<li><code>degradation_reason</code> (web_intelligence.py) — <span class="doc-comment-inline">Důvod degraded módu, pokud existuje.</span></li>
<li><code>queue_health</code> (web_intelligence.py) — <span class="doc-comment-inline">Read-only seam: queue pressure and aging status at a glance.</span></li>
<li><code>active_posture</code> (web_intelligence.py) — <span class="doc-comment-inline">Read-only seam: active vs queued posture.</span></li>
<li><code>completed_operations</code> (web_intelligence.py) — <span class="doc-comment-inline">Backward-compatible accessor for completed_operations (read-only copy).</span></li>
<li><code>completed_count</code> (web_intelligence.py) — <span class="doc-comment-inline">Read-only count of completed operations (bounded).</span></li>
<li><code>task_posture</code> (web_intelligence.py) — <span class="doc-comment-inline">Read-only snapshot of task ownership state.</span></li>
<li><code>get_system_metrics</code> (web_intelligence.py) — <span class="doc-comment-inline">Get comprehensive system metrics.</span></li>
<li><code>__post_init__</code> (temporal_archaeologist.py)</li>
<li><code>age_days</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Calculate age in days from now.</span></li>
<li><code>__post_init__</code> (temporal_archaeologist.py)</li>
<li><code>__aexit__</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Async context manager exit — pool manages session lifecycle.</span></li>
<li><code>_search_by_entity</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Search for archived versions by entity identifier.</span></li>
<li><code>_search_wayback_by_query</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Search Wayback by query string.</span></li>
<li><code>_search_common_crawl</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Search Common Crawl index.</span></li>
<li><code>get_statistics</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Get archaeologist statistics.</span></li>
<li><code>create_temporal_archaeologist</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Factory function for TemporalArchaeologist.</span></li>
<li><code>get_fingerprint_stats</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Return copy of fingerprint stats (for probe verification).</span></li>
<li><code>get_stats</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Return fingerprinting stats snapshot.</span></li>
<li><code>reset_stats</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Reset fingerprinting stats.</span></li>
<li><code>create_passive_fingerprint_adapter</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Factory for PassiveFingerprintAdapter.</span></li>
<li><code>reset_stats</code> (passive_fingerprint.py)</li>
<li><code>create_passive_tech_stack_adapter</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Factory for PassiveTechStackAdapter.</span></li>
<li><code>_get_tor_browser_ua</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Get Tor Browser User-Agent.</span></li>
<li><code>get_session</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Get httpx.AsyncClient configured for Tor.</span></li>
<li><code>__aexit__</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Async context manager exit - closes Tor connection.</span></li>
<li><code>get_statistics</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Get crawling statistics with bounded truth.</span></li>
<li><code>_default_serializer</code> (exposure_clients.py) — <span class="doc-comment-inline">Default JSON serializer pro LMDB cache.</span></li>
<li><code>_default_deserializer</code> (exposure_clients.py) — <span class="doc-comment-inline">Default JSON deserializer pro LMDB cache.</span></li>
<li><code>_write</code> (exposure_clients.py)</li>
<li><code>close</code> (exposure_clients.py) — <span class="doc-comment-inline">No-op — kept for API consistency with other clients.</span></li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>close</code> (exposure_clients.py) — <span class="doc-comment-inline">No-op — kept for API consistency.</span></li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>crack_hash</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Attempt to crack hash with dictionary attack.</span></li>
<li><code>detect_encryption</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Detect if data is encrypted.</span></li>
<li><code>analyze_certificate_security</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Analyze certificate security.</span></li>
<li><code>get_statistics</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Get cryptographic analysis statistics.</span></li>
<li><code>get_correlator_stats</code> (exposure_correlator.py) — <span class="doc-comment-inline">Return copy of correlator stats (for probe verification).</span></li>
<li><code>_check_with_sem</code> (exposure_correlator.py)</li>
<li><code>_scan_all</code> (exposure_correlator.py)</li>
<li><code>get_stats</code> (exposure_correlator.py) — <span class="doc-comment-inline">Return latest correlation stats.</span></li>
<li><code>create_exposure_correlator_adapter</code> (exposure_correlator.py) — <span class="doc-comment-inline">Factory for ExposureCorrelatorAdapter.</span></li>
<li><code>limited_search</code> (academic_discovery.py)</li>
<li><code>__hash__</code> (relationship_discovery.py)</li>
<li><code>__init__</code> (document_intelligence.py)</li>
<li><code>get_mean</code> (pattern_mining.py)</li>
<li><code>get_variance</code> (pattern_mining.py)</li>
<li><code>get_std</code> (pattern_mining.py)</li>
<li><code>count</code> (identity_stitching.py)</li>
<li><code>_compute_pair</code> (identity_stitching.py)</li>
<li><code>__len__</code> (identity_stitching.py)</li>
<li><code>to_dict</code> (archive_discovery.py)</li>
<li><code>is_archived</code> (archive_discovery.py)</li>
<li><code>domain</code> (archive_discovery.py)</li>
<li><code>to_dict</code> (archive_discovery.py)</li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>__enter__</code> (exposed_service_hunter.py)</li>
<li><code>to_dict</code> (academic_search.py)</li>
<li><code>to_dict</code> (academic_search.py)</li>
<li><code>__aenter__</code> (academic_search.py)</li>
<li><code>__aexit__</code> (academic_search.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>build_url</code> (open_source_collectors.py)</li>
<li><code>parse</code> (open_source_collectors.py)</li>
<li><code>build_url</code> (open_source_collectors.py)</li>
<li><code>build_url</code> (open_source_collectors.py)</li>
<li><code>_scrape_privatebin</code> (open_source_collectors.py)</li>
<li><code>_scrape_ghostbin</code> (open_source_collectors.py)</li>
<li><code>_scrape_0bin</code> (open_source_collectors.py)</li>
<li><code>__init__</code> (open_source_collectors.py)</li>
<li><code>to_dict</code> (temporal_archaeologist.py)</li>
<li><code>first_seen</code> (temporal_archaeologist.py)</li>
<li><code>last_seen</code> (temporal_archaeologist.py)</li>
<li><code>total_snapshots</code> (temporal_archaeologist.py)</li>
<li><code>to_dict</code> (temporal_archaeologist.py)</li>
<li><code>__init__</code> (passive_fingerprint.py)</li>
<li><code>__init__</code> (passive_fingerprint.py)</li>
<li><code>get_stats</code> (passive_fingerprint.py)</li>
<li><code>__init__</code> (dark_web_intelligence.py)</li>
<li><code>_make_key</code> (exposure_clients.py)</li>
<li><code>close</code> (exposure_clients.py)</li>
<li><code>close</code> (exposure_clients.py)</li>
<li><code>_get_session</code> (exposure_clients.py)</li>
<li><code>close</code> (exposure_clients.py)</li>
<li><code>__init__</code> (cryptographic_intelligence.py)</li>
<li><code>has_bucket</code> (exposure_correlator.py)</li>
<li><code>has_cert</code> (exposure_correlator.py)</li>
<li><code>has_jarm</code> (exposure_correlator.py)</li>
<li><code>has_dns</code> (exposure_correlator.py)</li>
<li><code>__init__</code> (exposure_correlator.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (224)</summary>
<ul>
<li><code>RelationshipDiscoveryEngine</code> (relationship_discovery.py)
<details><summary>Advanced relationship discovery and social network analysis engine.</summary>
<div class="doc-comment">
<p>Advanced relationship discovery and social network analysis engine.</p>
<p></p>
<p>This engine provides comprehensive capabilities for discovering and analyzing</p>
<p>relationships between entities, including social network analysis, community</p>
<p>detection, hidden path finding, and influence propagation modeling.</p>
<p></p>
<p>M1 8GB Optimizations:</p>
<p>- Uses scipy.sparse for large graphs to minimize memory usage</p>
<p>- Streaming graph construction for incremental updates</p>
<p>- Memory-efficient algorithms with lazy evaluation</p>
<p>- MLX acceleration where beneficial for matrix operations</p>
<p></p>
<p>Example:</p>
<p>engine = RelationshipDiscoveryEngine()</p>
<p></p>
<p># Add entities</p>
<p>engine.add_entity(Entity("user1", "person", {"name": "Alice"}))</p>
<p>engine.add_entity(Entity("user2", "person", {"name": "Bob"}))</p>
<p></p>
<p># Add relationships</p>
<p>engine.add_relationship(Relationship("user1", "user2", "knows", strength=0.8))</p>
<p></p>
<p># Analyze</p>
<p>centrality = engine.calculate_centrality("betweenness")</p>
<p>communities = engine.detect_communities()</p>
<p>paths = engine.find_hidden_paths("user1", "user2", max_depth=3)</p>
</div>
</details>
</li>
<li><code>UnifiedWebIntelligence</code> (web_intelligence.py)
<details><summary>Web intelligence helper — OSINT scraping and threat analysis utilities.</summary>
<div class="doc-comment">
<p>Web intelligence helper — OSINT scraping and threat analysis utilities.</p>
<p></p>
<p>Provides a bounded, lazy-initialized wrapper around Hledac's optional scraping</p>
<p>and OSINT components. This is a utility helper, not a canonical runtime</p>
<p>owner; all heavy orchestration lives in autonomous_orchestrator.</p>
<p></p>
<p>Key Features:</p>
<p>1. Bounded queue with priority aging</p>
<p>2. Lazy component initialization on first operation</p>
<p>3. Graceful degradation when optional dependencies are unavailable</p>
<p>4. Task ownership tracking with symmetric cleanup</p>
<p>5. Memory pressure awareness for M1 8GB environments</p>
</div>
</details>
</li>
<li><code>PatternMiningEngine</code> (pattern_mining.py)
<details><summary>Advanced pattern mining engine with M1 8GB optimization.</summary>
<div class="doc-comment">
<p>Advanced pattern mining engine with M1 8GB optimization.</p>
<p></p>
<p>Capabilities:</p>
<p>- Behavioral pattern detection</p>
<p>- Transaction flow analysis</p>
<p>- Temporal pattern mining</p>
<p>- Communication pattern extraction</p>
<p>- Structural pattern recognition</p>
<p>- Sequential pattern mining</p>
<p>- Anomaly detection</p>
<p></p>
<p>M1 Optimizations:</p>
<p>- Streaming algorithms for large datasets</p>
<p>- Efficient sliding windows</p>
<p>- Memory-efficient frequency counting</p>
<p>- MLX-accelerated correlation and FFT</p>
</div>
</details>
</li>
<li><code>IdentityStitchingEngine</code> (identity_stitching.py)
<details><summary>Advanced identity stitching engine for cross-platform identity linking.</summary>
<div class="doc-comment">
<p>Advanced identity stitching engine for cross-platform identity linking.</p>
<p></p>
<p>This engine provides comprehensive capabilities for:</p>
<p>- Linking identities across platforms using usernames, emails, and aliases</p>
<p>- Probabilistic identity matching with multiple signals</p>
<p>- Username similarity using fuzzy string matching</p>
<p>- Writing style similarity using lightweight text analysis</p>
<p>- Temporal overlap analysis</p>
<p>- Network overlap analysis</p>
<p>- Identity graph construction and community detection</p>
<p></p>
<p>M1 8GB Optimizations:</p>
<p>- Uses rapidfuzz for fast C-based string matching</p>
<p>- No heavy ML models - only lightweight sklearn TF-IDF if available</p>
<p>- Memory-efficient graph operations with NetworkX</p>
<p>- Streaming processing for large datasets</p>
<p>- Lazy evaluation for expensive operations</p>
<p></p>
<p>Example:</p>
<p>engine = IdentityStitchingEngine(similarity_threshold=0.7)</p>
<p></p>
<p># Add profiles</p>
<p>profile = IdentityProfile(</p>
<p>id="user1",</p>
<p>primary_name="Alice Smith",</p>
<p>emails=["alice@example.com"],</p>
<p>)</p>
<p>profile.add_username("twitter", "alice_smith")</p>
<p>profile.add_username("github", "alicecodes")</p>
<p>engine.add_profile(profile)</p>
<p></p>
<p># Find matches</p>
<p>matches = engine.find_matches("user1")</p>
<p></p>
<p># Stitch identities</p>
<p>stitched = engine.stitch_identities(match_threshold=0.8)</p>
</div>
</details>
</li>
<li><code>TemporalArchaeologist</code> (temporal_archaeologist.py)
<details><summary>Advanced temporal content recovery and timeline reconstruction system.</summary>
<div class="doc-comment">
<p>Advanced temporal content recovery and timeline reconstruction system.</p>
<p></p>
<p>This class provides comprehensive tools for:</p>
<p>- Recovering deleted content from multiple archive sources</p>
<p>- Reconstructing version history from fragmented data</p>
<p>- Tracking entity identity changes over time</p>
<p>- Finding correlations between events across time</p>
<p>- Detecting temporal anomalies (gaps, sudden changes, disappearances)</p>
<p>- Building timelines from scattered archival sources</p>
<p></p>
<p>M1 8GB Optimizations:</p>
<p>- Async concurrent queries to multiple archives</p>
<p>- Streaming content processing with chunked reading</p>
<p>- Incremental timeline building to minimize memory usage</p>
<p>- Memory-efficient diff algorithms using rolling hashes</p>
</div>
</details>
</li>
<li><code>BlockchainForensics</code> (blockchain_analyzer.py)
<details><summary>Advanced blockchain forensics and analysis tool.</summary>
<div class="doc-comment">
<p>Advanced blockchain forensics and analysis tool.</p>
<p></p>
<p>M1 8GB Optimized:</p>
<p>- Async API calls with connection pooling</p>
<p>- LRU caching for API responses (5 min TTL)</p>
<p>- Streaming processing for large transaction histories</p>
<p>- Minimal memory footprint</p>
</div>
</details>
</li>
<li><code>DarkWebCrawler</code> (dark_web_intelligence.py)
<details><summary>Advanced dark web crawler for OSINT research.</summary>
<div class="doc-comment">
<p>Advanced dark web crawler for OSINT research.</p>
<p></p>
<p>Crawls Tor hidden services and extracts intelligence:</p>
<p>- Hidden service enumeration</p>
<p>- Content extraction and indexing</p>
<p>- Cryptocurrency address harvesting</p>
<p>- PGP key discovery</p>
<p>- Link graph analysis</p>
</div>
</details>
</li>
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
<li><code>WorkflowOrchestrator</code> (workflow_orchestrator.py)
<details><summary>Orchestrates multi-module analysis workflows.</summary>
<div class="doc-comment">
<p>Orchestrates multi-module analysis workflows.</p>
<p></p>
<p>Coordinates execution of analysis modules, correlates results,</p>
<p>detects anomalies, and generates comprehensive reports.</p>
<p></p>
<p>Example:</p>
<p>orchestrator = WorkflowOrchestrator(main_orchestrator)</p>
<p>plan = WorkflowPlan(modules=["stego", "metadata", "encoding"])</p>
<p>report = await orchestrator.execute_workflow(plan, input_data)</p>
<p>print(report.to_json())</p>
</div>
</details>
</li>
<li><code>MLXLongContextAnalyzer</code> (document_intelligence.py)
<details><summary>MLX-powered analysis for ultra-large documents on M1 8GB.</summary>
<div class="doc-comment">
<p>MLX-powered analysis for ultra-large documents on M1 8GB.</p>
<p></p>
<p>Capabilities:</p>
<p>- Chunking with intelligent overlap for context preservation</p>
<p>- Cross-document entity resolution</p>
<p>- Timeline reconstruction from large datasets</p>
<p>- MLX-accelerated similarity matching</p>
<p>- Memory-efficient streaming processing</p>
<p></p>
<p>M1 Optimized:</p>
<p>- Streaming processing to keep memory &lt; 5.5GB</p>
<p>- MLX lazy evaluation for efficiency</p>
<p>- Smart chunk sizing based on available RAM</p>
</div>
</details>
</li>
<li><code>CVIntelligenceClient</code> (exposure_clients.py)
<details><summary>CVE/Vulnerability Intelligence via OSV.dev + NVD API 2.0 + EPSS.</summary>
<div class="doc-comment">
<p>CVE/Vulnerability Intelligence via OSV.dev + NVD API 2.0 + EPSS.</p>
<p></p>
<p>OSV.dev Batch API (priority):</p>
<p>POST https://api.osv.dev/v1/querybatch</p>
<p>Streaming response, max 200 CVEs, batches of 20.</p>
<p></p>
<p>NVD API 2.0 fallback (if OSV returns 0 results):</p>
<p>GET https://services.nvd.nist.gov/rest/json/cves/2.0</p>
<p>Rate limited: Rust NvdRateLimiter token bucket (5 req/30s bez API key,</p>
<p>50 req/30s s API key) — ISSUE #016.</p>
<p></p>
<p>EPSS enrichment:</p>
<p>GET https://api.first.org/data/v1/epss?cve={cve_id}</p>
<p>Adds epss_score, percentile; EPSS &gt;0.7 → IMMEDIATE_ACTION flag.</p>
<p></p>
<p>M1 invariants:</p>
<p>- get_intelligence_session() for HTTP (shared aiohttp session)</p>
<p>- LMDB cache with 24h TTL (NVD data changes daily)</p>
<p>- AsyncIterator[dict] for streaming results</p>
<p>- No asyncio.run() inside async functions</p>
<p>- Generator pattern with chunk processing</p>
<p>- Rust NvdRateLimiter (crossbeam-channel, ~zero RAM, no GIL)</p>
</div>
</details>
</li>
<li><code>ArchiveResurrector</code> (archive_discovery.py)
<details><summary>Advanced web archive content recovery system.</summary>
<div class="doc-comment">
<p>Advanced web archive content recovery system.</p>
<p></p>
<p>Features:</p>
<p>- Wayback Machine CDX API integration</p>
<p>- Search engine cache checking</p>
<p>- Social media archive access</p>
<p>- Content quality assessment</p>
<p>- Metadata extraction</p>
<p>- Concurrent processing</p>
<p></p>
<p>Integrated from stealth_osint for universal orchestrator.</p>
</div>
</details>
</li>
<li><code>AcademicSearchEngine</code> (academic_search.py)
<details><summary>Main engine for Multi-Source Academic Search.</summary>
<div class="doc-comment">
<p>Main engine for Multi-Source Academic Search.</p>
<p></p>
<p>Coordinates query expansion, source selection, parallel execution,</p>
<p>and result deduplication.</p>
</div>
</details>
</li>
<li><code>DocumentIntelligenceEngine</code> (document_intelligence.py)
<details><summary>Main engine for document intelligence analysis.</summary>
<div class="doc-comment">
<p>Main engine for document intelligence analysis.</p>
<p></p>
<p>Provides unified interface for analyzing all document types.</p>
</div>
</details>
</li>
<li><code>ClassicalCryptanalysis</code> (cryptographic_intelligence.py)
<details><summary>Cryptanalysis of classical (pre-computer) ciphers.</summary>
<div class="doc-comment">
<p>Cryptanalysis of classical (pre-computer) ciphers.</p>
<p></p>
<p>Essential for CTF challenges, historical cryptanalysis,</p>
<p>and analyzing simple obfuscation in OSINT.</p>
</div>
</details>
</li>
<li><code>NetworkReconnaissance</code> (network_reconnaissance.py)
<details><summary>Main network reconnaissance engine.</summary>
<div class="doc-comment">
<p>Main network reconnaissance engine.</p>
<p></p>
<p>Combines all network intelligence gathering capabilities.</p>
</div>
</details>
</li>
<li><code>PDFAnalyzer</code> (document_intelligence.py)
<details><summary>Advanced PDF document analyzer.</summary>
<div class="doc-comment">
<p>Advanced PDF document analyzer.</p>
<p></p>
<p>Extracts metadata, text, embedded objects, and forensic artifacts.</p>
</div>
</details>
</li>
<li><code>ExposedServiceHunter</code> (exposed_service_hunter.py)
<details><summary>Main exposed service hunter.</summary>
<div class="doc-comment">
<p>Main exposed service hunter.</p>
<p></p>
<p>Combines all exposed service discovery capabilities:</p>
<p>- S3 bucket enumeration</p>
<p>- Database port scanning</p>
<p>- GraphQL introspection</p>
<p>- Certificate transparency</p>
<p>- Docker/Kubernetes API detection</p>
<p></p>
<p>M1 Optimized: Async I/O, connection pooling, minimal memory</p>
<p></p>
<p>Example:</p>
<p>&gt;&gt;&gt; hunter = ExposedServiceHunter()</p>
<p>&gt;&gt;&gt; results = await hunter.hunt("example.com")</p>
<p>&gt;&gt;&gt; print(f"Found {len(results['s3_buckets'])} S3 buckets")</p>
</div>
</details>
</li>
<li><code>DeepForensicsAnalyzer</code> (document_intelligence.py)
<details><summary>Advanced forensics for images - EXIF, ELA, steganography detection.</summary>
<div class="doc-comment">
<p>Advanced forensics for images - EXIF, ELA, steganography detection.</p>
<p></p>
<p>Uses shared ProcessPoolExecutor for CPU-bound operations (M1 8GB safe: max 2 workers).</p>
<p>Steganography detection uses async subprocess pool via StegdetectServer.</p>
</div>
</details>
</li>
<li><code>OfficeDocumentAnalyzer</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyzer for Microsoft Office and OpenDocument files.</span></li>
<li><code>DNSEnumerator</code> (network_reconnaissance.py)
<details><summary>Advanced DNS enumeration.</summary>
<div class="doc-comment">
<p>Advanced DNS enumeration.</p>
<p></p>
<p>Comprehensive DNS reconnaissance with multiple techniques.</p>
</div>
</details>
</li>
<li><code>ImageAnalyzer</code> (document_intelligence.py)
<details><summary>Advanced image analysis for OSINT.</summary>
<div class="doc-comment">
<p>Advanced image analysis for OSINT.</p>
<p></p>
<p>Extracts EXIF data, GPS coordinates, and performs image forensics.</p>
</div>
</details>
</li>
<li><code>OpenSourceCollectors</code> (open_source_collectors.py)
<details><summary>Unified collector for open-source intelligence sources.</summary>
<div class="doc-comment">
<p>Unified collector for open-source intelligence sources.</p>
<p></p>
<p>Integrates with:</p>
<p>- Session: httpx.AsyncClient()</p>
<p>- Transport: async_fetch_public_text()</p>
<p>- Memory: M1ResourceGovernor.sidecar_admission()</p>
<p>- Confidence: source_family tagging in all findings</p>
</div>
</details>
</li>
<li><code>DatabasePortScanner</code> (exposed_service_hunter.py)
<details><summary>Scanner for exposed database ports.</summary>
<div class="doc-comment">
<p>Scanner for exposed database ports.</p>
<p></p>
<p>Checks common database ports for open access.</p>
<p>Uses lightweight TCP connection checks.</p>
</div>
</details>
</li>
<li><code>SemanticScholarAdapter</code> (academic_search.py) — <span class="doc-comment-inline">Adapter for searching Semantic Scholar.</span></li>
<li><code>HashAnalyzer</code> (cryptographic_intelligence.py)
<details><summary>Analyze and identify hash types.</summary>
<div class="doc-comment">
<p>Analyze and identify hash types.</p>
<p></p>
<p>Supports hash identification, entropy analysis,</p>
<p>and basic cracking attempts.</p>
</div>
</details>
</li>
<li><code>ContainerAPIExplorer</code> (exposed_service_hunter.py)
<details><summary>Docker and Kubernetes API explorer.</summary>
<div class="doc-comment">
<p>Docker and Kubernetes API explorer.</p>
<p></p>
<p>Detects exposed container orchestration APIs.</p>
</div>
</details>
</li>
<li><code>EncryptionDetector</code> (cryptographic_intelligence.py)
<details><summary>Detect if data is encrypted and identify possible cipher.</summary>
<div class="doc-comment">
<p>Detect if data is encrypted and identify possible cipher.</p>
<p></p>
<p>Uses statistical analysis to detect encryption.</p>
</div>
</details>
</li>
<li><code>CertificateAnalyzer</code> (cryptographic_intelligence.py)
<details><summary>Analyze X.509 certificates.</summary>
<div class="doc-comment">
<p>Analyze X.509 certificates.</p>
<p></p>
<p>Parse and analyze SSL/TLS certificates for OSINT.</p>
</div>
</details>
</li>
<li><code>ArxivAdapter</code> (academic_search.py) — <span class="doc-comment-inline">Adapter for searching ArXiv.</span></li>
<li><code>CensysClient</code> (exposure_clients.py)
<details><summary>Censys API client s LMDB cache.</summary>
<div class="doc-comment">
<p>Censys API client s LMDB cache.</p>
<p></p>
<p>Cache key: censys:{query_hash}</p>
<p>TTL: 7 dní</p>
<p></p>
<p>Bez CENSYS_API_ID/CENSYS_API_SECRET: LMDB-only mode.</p>
</div>
</details>
</li>
<li><code>S3BucketEnumerator</code> (exposed_service_hunter.py)
<details><summary>S3 bucket enumeration using common naming patterns.</summary>
<div class="doc-comment">
<p>S3 bucket enumeration using common naming patterns.</p>
<p></p>
<p>Uses HTTP HEAD requests to check bucket existence and permissions.</p>
<p>No AWS credentials required.</p>
</div>
</details>
</li>
<li><code>SemanticScholarClient</code> (academic_search.py)
<details><summary>Semantic Scholar Graph API + ArXiv API — výzkumné papery.</summary>
<div class="doc-comment">
<p>Semantic Scholar Graph API + ArXiv API — výzkumné papery.</p>
<p>Zadarmo bez klíče (1000 req/5min). Neindexováno běžnými OSINT nástroji.</p>
<p>Technical details z research paperů = primární CVE/malware zdroj.</p>
</div>
</details>
</li>
<li><code>CrossrefAdapter</code> (academic_search.py) — <span class="doc-comment-inline">Adapter for searching Crossref.</span></li>
<li><code>StegdetectServer</code> (document_intelligence.py) — <span class="doc-comment-inline">Persistent stegdetect process with semaphore pool for concurrent analysis.</span></li>
<li><code>GraphQLIntrospector</code> (exposed_service_hunter.py)
<details><summary>GraphQL introspection discovery.</summary>
<div class="doc-comment">
<p>GraphQL introspection discovery.</p>
<p></p>
<p>Discovers GraphQL endpoints and extracts schema information.</p>
</div>
</details>
</li>
<li><code>ComprehensiveReport</code> (workflow_orchestrator.py)
<details><summary>Comprehensive analysis report from workflow execution.</summary>
<div class="doc-comment">
<p>Comprehensive analysis report from workflow execution.</p>
<p></p>
<p>Attributes:</p>
<p>input_summary: Summary of input data</p>
<p>module_results: Results from each analysis module</p>
<p>correlations: Cross-module correlation report</p>
<p>anomalies: List of detected anomalies</p>
<p>verdict: Final verdict ("CLEAN", "SUSPICIOUS", "HIGH_RISK")</p>
<p>confidence: Overall confidence score</p>
<p>recommendations: List of actionable recommendations</p>
<p>timeline: Timeline of analysis events</p>
<p>export_data: Data formatted for export</p>
</div>
</details>
</li>
<li><code>WaybackCDX</code> (archive_discovery.py)
<details><summary>Wayback Machine CDX API — low-level domain/URL snapshot discovery.</summary>
<div class="doc-comment">
<p>Wayback Machine CDX API — low-level domain/URL snapshot discovery.</p>
<p>ZADARMO, bez API klíče. Unikátní zdroj: smazaný obsah (C2 configs,</p>
<p>leaked keys, expired phishing domains).</p>
<p>M1: pure aiohttp async, orjson, xxhash cache 24h.</p>
</div>
</details>
</li>
<li><code>TorProxyManager</code> (dark_web_intelligence.py)
<details><summary>Manages Tor proxy connections for stealth crawling.</summary>
<div class="doc-comment">
<p>Manages Tor proxy connections for stealth crawling.</p>
<p></p>
<p>Requires Tor to be running locally (brew install tor)</p>
<p></p>
<p>F4XX: migrated from aiohttp + aiohttp_socks to httpx + httpx-socks.</p>
</div>
</details>
</li>
<li><code>APICache</code> (exposed_service_hunter.py)
<details><summary>Simple sqlite-based API cache with TTL.</summary>
<div class="doc-comment">
<p>Simple sqlite-based API cache with TTL.</p>
<p></p>
<p>Used for rate-limited APIs like Shodan and Censys.</p>
</div>
</details>
</li>
<li><code>WHOISLookup</code> (network_reconnaissance.py)
<details><summary>WHOIS data retrieval.</summary>
<div class="doc-comment">
<p>WHOIS data retrieval.</p>
<p></p>
<p>Fetches domain registration information from WHOIS servers.</p>
</div>
</details>
</li>
<li><code>GNNPredictorWrapper</code> (relationship_discovery.py) — <span class="doc-comment-inline">Wrapper for GNN predictor with training and prediction.</span></li>
<li><code>_IdentityCache</code> (identity_stitching.py)
<details><summary>Symmetric-key LRU cache backed by PyCacheDict.</summary>
<div class="doc-comment">
<p>Symmetric-key LRU cache backed by PyCacheDict.</p>
<p></p>
<p>Wraps PyCacheDict to add:</p>
<p>- Symmetric key normalization: (A,B) and (B,A) map to same slot</p>
<p>- Memory-pressure eviction: psutil-based 50% eviction when RSS exceeds threshold</p>
<p></p>
<p>PyCacheDict provides: TTL, thread-safe RLock, hit/miss/eviction stats.</p>
</div>
</details>
</li>
<li><code>CryptographicIntelligence</code> (cryptographic_intelligence.py)
<details><summary>Main cryptographic intelligence engine.</summary>
<div class="doc-comment">
<p>Main cryptographic intelligence engine.</p>
<p></p>
<p>Combines all cryptographic analysis capabilities.</p>
</div>
</details>
</li>
<li><code>CertificateTransparency</code> (exposed_service_hunter.py)
<details><summary>Certificate Transparency log queries via crt.sh.</summary>
<div class="doc-comment">
<p>Certificate Transparency log queries via crt.sh.</p>
<p></p>
<p>Queries the public crt.sh service for certificate information.</p>
<p>No API key required.</p>
</div>
</details>
</li>
<li><code>ExposureCache</code> (exposure_clients.py)
<details><summary>LMDB-backed cache pro exposure klienty.</summary>
<div class="doc-comment">
<p>LMDB-backed cache pro exposure klienty.</p>
<p>Single-writer přes DB_EXECUTOR.</p>
<p>TTL: 7 dní.</p>
</div>
</details>
</li>
<li><code>MalwareBazaarClient</code> (exposure_clients.py)
<details><summary>Abuse.ch MalwareBazaar — hash intel + malware family tags.</summary>
<div class="doc-comment">
<p>Abuse.ch MalwareBazaar — hash intel + malware family tags.</p>
<p></p>
<p>M1: pure aiohttp, 1h cache, orjson.</p>
</div>
</details>
</li>
<li><code>WaybackMachineClient</code> (archive_discovery.py) — <span class="doc-comment-inline">Client for Internet Archive Wayback Machine.</span></li>
<li><code>DHTProbe</code> (network_reconnaissance.py)
<details><summary>BitTorrent DHT — discovery metadata z P2P sítě.</summary>
<div class="doc-comment">
<p>BitTorrent DHT — discovery metadata z P2P sítě.</p>
<p>UDP asyncio, bootstrap přes router.bittorrent.com.</p>
<p>info_hash jména → PatternMatcher → malware infrastructure.</p>
<p>Zdroj neindexovaný žádným komerčním nástrojem.</p>
</div>
</details>
</li>
<li><code>ShodanClient</code> (exposure_clients.py)
<details><summary>Shodan API client s LMDB cache.</summary>
<div class="doc-comment">
<p>Shodan API client s LMDB cache.</p>
<p></p>
<p>Cache key: shodan:{ip}</p>
<p>TTL: 7 dní</p>
<p></p>
<p>Bez SHODAN_API_KEY: LMDB-only mode, žádné HTTP volání.</p>
</div>
</details>
</li>
<li><code>PassiveDNSClient</code> (network_reconnaissance.py)
<details><summary>Async passive DNS client using dnspython asyncresolver.</summary>
<div class="doc-comment">
<p>Async passive DNS client using dnspython asyncresolver.</p>
<p></p>
<p>M1: pure async, no blocking socket calls.</p>
</div>
</details>
</li>
<li><code>GitHubCodeSearchClient</code> (exposure_clients.py)
<details><summary>GitHub Code Search API — CVE PoC + malware samples.</summary>
<div class="doc-comment">
<p>GitHub Code Search API — CVE PoC + malware samples.</p>
<p></p>
<p>M1: aiohttp async, 1h xxhash cache, orjson serialization.</p>
<p>Without GITHUB_TOKEN: 60 req/h unauthenticated limit.</p>
</div>
</details>
</li>
<li><code>SSLAnalyzer</code> (network_reconnaissance.py) — <span class="doc-comment-inline">SSL/TLS certificate analysis.</span></li>
<li><code>ExposureCorrelatorAdapter</code> (exposure_correlator.py)
<details><summary>F202C: Bounded exposure correlation adapter.</summary>
<div class="doc-comment">
<p>F202C: Bounded exposure correlation adapter.</p>
<p></p>
<p>Wraps the correlation pipeline with M1-safe bounds and fail-soft guarantees.</p>
</div>
</details>
</li>
<li><code>ArchiveDiscovery</code> (archive_discovery.py)
<details><summary>Main archive discovery orchestrator.</summary>
<div class="doc-comment">
<p>Main archive discovery orchestrator.</p>
<p></p>
<p>Combines multiple archival sources for comprehensive</p>
<p>historical content discovery.</p>
</div>
</details>
</li>
<li><code>IdentityProfile</code> (identity_stitching.py)
<details><summary>Represents a unified identity profile across platforms.</summary>
<div class="doc-comment">
<p>Represents a unified identity profile across platforms.</p>
<p></p>
<p>Attributes:</p>
<p>id: Unique identifier for this profile</p>
<p>primary_name: Primary display name</p>
<p>aliases: List of known aliases/alternate names</p>
<p>emails: List of associated email addresses</p>
<p>usernames: List of platform-specific usernames</p>
<p>confidence: Overall confidence score (0-1)</p>
<p>evidence: List of evidence strings supporting this profile</p>
<p>attributes: Additional metadata</p>
<p>created_at: Profile creation timestamp</p>
<p>updated_at: Last update timestamp</p>
</div>
</details>
</li>
<li><code>PastebinMonitorClient</code> (archive_discovery.py)
<details><summary>Pastebin scraping API — free tier, 1 req/min.</summary>
<div class="doc-comment">
<p>Pastebin scraping API — free tier, 1 req/min.</p>
<p>Filter pastes by keyword and store in evidence.</p>
</div>
</details>
</li>
<li><code>CorrelationResult</code> (workflow_orchestrator.py)
<details><summary>Lightweight correlation result from findings analysis.</summary>
<div class="doc-comment">
<p>Lightweight correlation result from findings analysis.</p>
<p></p>
<p>Attributes:</p>
<p>themes: Grouped findings by correlation theme</p>
<p>risk_score: Overall risk score (0.0-1.0)</p>
<p>risk_buckets: Findings bucketed by risk level</p>
<p>top_themes: Top 5 most significant themes sorted by weight</p>
<p>anomaly_count: Number of detected anomalies</p>
<p>verdict: Risk verdict string</p>
<p></p>
<p># --- NEW: actionable condensation ---</p>
<p>source_themes: dict[str, list[str]]           # source -&gt; list of theme keys</p>
<p>top_entities: list[dict[str, Any]]            # extracted IOCs (domain/ip/hash/url)</p>
<p>repeated_domains: list[str]                   # domains seen across &gt;1 finding</p>
<p>repeated_iocs: list[dict[str, Any]]          # IOCs appearing &gt;1 time</p>
<p>dominant_cluster: str | None               # theme with most high-severity findings</p>
<p>high_risk_branch: list[dict[str, Any]]        # critical/high findings with infra hints</p>
<p>theme_source_overlap: dict[str, list[str]]   # theme -&gt; sources contributing</p>
<p>campaign_hints: list[dict[str, Any]]          # findings suggesting same campaign</p>
<p>coupling_pairs: list[tuple[str, str]]          # (entity, related_entity) pairs</p>
<p>so_what: str                                   # one-liner operator takeaway</p>
<p></p>
<p># --- SECOND-ORDER CONDENSATION (sprint delta) ---</p>
<p>cross_source_confidence: float = 0.0       # 0.0-1.0: multi-source corroboration score</p>
<p>corroborated_iocs: list[dict[str, Any]] = field(default_factory=list)  # IOCs with 2+ source evidence</p>
<p>top_priority_pivots: list[dict[str, Any]] = field(default_factory=list)  # bounded action shortlist</p>
<p>campaign_confidence: float = 0.0            # 0.0-1.0: campaign cluster confidence</p>
</div>
</details>
</li>
<li><code>CryptocurrencyAnalyzer</code> (dark_web_intelligence.py)
<details><summary>Analyzes cryptocurrency addresses found in dark web content.</summary>
<div class="doc-comment">
<p>Analyzes cryptocurrency addresses found in dark web content.</p>
<p></p>
<p>Tracks transactions, balances (where possible), and relationships.</p>
</div>
</details>
</li>
<li><code>GreyNoiseClient</code> (exposure_clients.py)
<details><summary>GreyNoise Community API — IP classification bez API klíče.</summary>
<div class="doc-comment">
<p>GreyNoise Community API — IP classification bez API klíče.</p>
<p>https://api.greynoise.io/v3/community/{ip}</p>
<p>Klasifikuje IP jako: malicious / benign / unknown.</p>
<p>Enrichment dat: scanner_type, tags, organization.</p>
</div>
</details>
</li>
<li><code>GitHubHistoricalClient</code> (archive_discovery.py) — <span class="doc-comment-inline">Client for GitHub historical commits.</span></li>
<li><code>ArchiveTodayClient</code> (archive_discovery.py) — <span class="doc-comment-inline">Client for Archive.today / archive.ph.</span></li>
<li><code>GitHubDorkingClient</code> (archive_discovery.py)
<details><summary>GitHub REST API search bez tokenu — rate limit 10 req/min.</summary>
<div class="doc-comment">
<p>GitHub REST API search bez tokenu — rate limit 10 req/min.</p>
<p>Pro použití s GitHub Advanced Search operators v query string.</p>
</div>
</details>
</li>
<li><code>_UnionFind</code> (identity_stitching.py) — <span class="doc-comment-inline">Lightweight Union-Find s path compression + rank union.</span></li>
<li><code>AcademicPaper</code> (academic_discovery.py) — <span class="doc-comment-inline">Structured academic paper result.</span></li>
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
<li><code>SourcePerformance</code> (academic_search.py) — <span class="doc-comment-inline">Performance metrics for a source.</span></li>
<li><code>Relationship</code> (relationship_discovery.py) — <span class="doc-comment-inline">Represents a relationship between two entities.</span></li>
<li><code>SlidingWindowCounter</code> (pattern_mining.py) — <span class="doc-comment-inline">Memory-efficient sliding window frequency counter.</span></li>
<li><code>BaseSourceAdapter</code> (academic_search.py) — <span class="doc-comment-inline">Abstract base class for search source adapters.</span></li>
<li><code>CipherType</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Types of ciphers supported.</span></li>
<li><code>LSHLinkPredictor</code> (relationship_discovery.py) — <span class="doc-comment-inline">Fast candidate generation for link prediction using MinHash LSH.</span></li>
<li><code>IPFSClient</code> (archive_discovery.py) — <span class="doc-comment-inline">Client for IPFS gateways.</span></li>
<li><code>EntityTimeline</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Complete timeline for an entity.</span></li>
<li><code>DocumentMetadata</code> (document_intelligence.py) — <span class="doc-comment-inline">Comprehensive document metadata.</span></li>
<li><code>IdentityMatch</code> (identity_stitching.py)
<details><summary>Represents a match between two identity profiles.</summary>
<div class="doc-comment">
<p>Represents a match between two identity profiles.</p>
<p></p>
<p>Attributes:</p>
<p>profile_a: ID of first profile</p>
<p>profile_b: ID of second profile</p>
<p>match_score: Overall match score (0-1)</p>
<p>match_signals: Dictionary of individual signal scores</p>
<p>confidence: Confidence level (high, medium, low)</p>
<p>evidence: List of evidence supporting the match</p>
</div>
</details>
</li>
<li><code>DiscoveredEndpoint</code> (archive_discovery.py) — <span class="doc-comment-inline">Discovered endpoint with metadata.</span></li>
<li><code>_PrivateBinAdapter</code> (open_source_collectors.py)
<details><summary>PrivateBin v2 → v1 fallback + encrypted-paste marker detection.</summary>
<div class="doc-comment">
<p>PrivateBin v2 → v1 fallback + encrypted-paste marker detection.</p>
<p></p>
<p>Behavior preserved bit-for-bit from the original _scrape_privatebin:</p>
<p>- try v2 endpoint first, then v1</p>
<p>- if response has both 'ct' and 'adata' → encrypted marker</p>
<p>- if response has 'content' → return its value</p>
<p>- on any exception in the parse of v2 → fall through to v1</p>
</div>
</details>
</li>
<li><code>StitchedIdentity</code> (identity_stitching.py)
<details><summary>Represents a stitched identity combining multiple profiles.</summary>
<div class="doc-comment">
<p>Represents a stitched identity combining multiple profiles.</p>
<p></p>
<p>Attributes:</p>
<p>id: Unique identifier for stitched identity</p>
<p>profile_ids: IDs of constituent profiles</p>
<p>primary_profile: ID of primary profile</p>
<p>merged_names: All names from constituent profiles</p>
<p>merged_emails: All emails from constituent profiles</p>
<p>merged_usernames: All usernames from constituent profiles</p>
<p>stitch_confidence: Confidence in the stitching (0-1)</p>
<p>match_evidence: Evidence supporting the stitch</p>
</div>
</details>
</li>
<li><code>PassiveFingerprintAdapter</code> (passive_fingerprint.py)
<details><summary>F204G: Bounded passive fingerprinting adapter.</summary>
<div class="doc-comment">
<p>F204G: Bounded passive fingerprinting adapter.</p>
<p></p>
<p>Wraps the fingerprinting pipeline with M1-safe bounds and fail-soft guarantees.</p>
</div>
</details>
</li>
<li><code>GeoLocation</code> (document_intelligence.py) — <span class="doc-comment-inline">GPS coordinates extracted from EXIF.</span></li>
<li><code>StreamingStatistics</code> (pattern_mining.py) — <span class="doc-comment-inline">Streaming mean and variance calculation (Welford's algorithm).</span></li>
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
<li><code>HashType</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Identified hash types.</span></li>
<li><code>PasteSiteAdapter</code> (open_source_collectors.py)
<details><summary>Contract every paste-site adapter must satisfy (duck-typed).</summary>
<div class="doc-comment">
<p>Contract every paste-site adapter must satisfy (duck-typed).</p>
<p></p>
<p>Concrete adapters are frozen dataclasses that expose:</p>
<p>site_id: str        — registry key, used for cache/inflight dedup</p>
<p>host:   str         — used for per-host Semaphore</p>
<p>timeout_s: float    — async_fetch_public_text timeout</p>
<p>max_bytes: int      — async_fetch_public_text max response size</p>
<p>build_url(paste_id) -&gt; str | list[str]</p>
<p>parse(body, paste_id) -&gt; str | None</p>
</div>
</details>
</li>
<li><code>IntelligenceResult</code> (web_intelligence.py) — <span class="doc-comment-inline">Comprehensive intelligence result.</span></li>
<li><code>_ZeroBinAdapter</code> (open_source_collectors.py) — <span class="doc-comment-inline">0bin HTML page → extract &lt;pre class='paste-content'&gt; with len &gt; 10.</span></li>
<li><code>ArchivedVersion</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Represents a single archived version of content.</span></li>
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
<li><code>CDXSnapshot</code> (archive_discovery.py) — <span class="doc-comment-inline">CDX API snapshot result.</span></li>
<li><code>PassiveTechStackAdapter</code> (passive_fingerprint.py) — <span class="doc-comment-inline">R11: Bounded passive tech-stack extraction adapter.</span></li>
<li><code>Asset</code> (exposure_correlator.py) — <span class="doc-comment-inline">An asset (host, domain, IP) with collected signals.</span></li>
<li><code>Entity</code> (relationship_discovery.py) — <span class="doc-comment-inline">Represents an entity in the relationship graph.</span></li>
<li><code>UsernameEntry</code> (identity_stitching.py) — <span class="doc-comment-inline">Represents a username on a specific platform.</span></li>
<li><code>QueryAnalysis</code> (academic_search.py) — <span class="doc-comment-inline">Analysis of a query.</span></li>
<li><code>WHOISData</code> (network_reconnaissance.py) — <span class="doc-comment-inline">WHOIS lookup results.</span></li>
<li><code>HiddenService</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Represents a discovered hidden service.</span></li>
<li><code>CertificateInfo</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Parsed certificate information.</span></li>
<li><code>EXIFData</code> (document_intelligence.py) — <span class="doc-comment-inline">Comprehensive EXIF data from images.</span></li>
<li><code>WalletAnalysis</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Comprehensive analysis of a wallet address.</span></li>
<li><code>ConnectionPath</code> (relationship_discovery.py) — <span class="doc-comment-inline">Represents a path between two entities through the graph.</span></li>
<li><code>AffinityMatrix</code> (relationship_discovery.py) — <span class="doc-comment-inline">Represents affinity scores between entities of a specific type.</span></li>
<li><code>Transaction</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Represents a blockchain transaction.</span></li>
<li><code>Finding</code> (workflow_orchestrator.py)
<details><summary>Represents a finding from cross-module analysis.</summary>
<div class="doc-comment">
<p>Represents a finding from cross-module analysis.</p>
<p></p>
<p>Attributes:</p>
<p>finding_type: Type of finding (e.g., "pattern", "anomaly")</p>
<p>description: Human-readable description of the finding</p>
<p>severity: Severity level ("low", "medium", "high", "critical")</p>
<p>confidence: Confidence score (0.0-1.0)</p>
<p>modules: List of modules that contributed to this finding</p>
</div>
</details>
</li>
<li><code>IntelligenceConfig</code> (workflow_orchestrator.py)
<details><summary>Configuration for workflow orchestrator.</summary>
<div class="doc-comment">
<p>Configuration for workflow orchestrator.</p>
<p></p>
<p>Attributes:</p>
<p>module_timeout: Timeout per module in seconds</p>
<p>max_parallel_modules: Maximum parallel modules</p>
<p>enable_correlation: Whether to enable cross-module correlation</p>
<p>enable_anomaly_detection: Whether to enable anomaly detection</p>
<p>risk_thresholds: Risk score thresholds for verdicts</p>
</div>
</details>
</li>
<li><code>SourceConfig</code> (academic_search.py) — <span class="doc-comment-inline">Configuration for a search source.</span></li>
<li><code>AcademicSearchResult</code> (academic_search.py) — <span class="doc-comment-inline">Complete academic search result.</span></li>
<li><code>RelationshipType</code> (relationship_discovery.py) — <span class="doc-comment-inline">Types of relationships between entities.</span></li>
<li><code>DarkWebContent</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Content extracted from dark web.</span></li>
<li><code>ResurrectionResult</code> (archive_discovery.py) — <span class="doc-comment-inline">Result of content resurrection (from stealth_osint integration)</span></li>
<li><code>ArchiveResult</code> (archive_discovery.py) — <span class="doc-comment-inline">Result from archive discovery.</span></li>
<li><code>Anomaly</code> (workflow_orchestrator.py)
<details><summary>Represents an anomaly detected during analysis.</summary>
<div class="doc-comment">
<p>Represents an anomaly detected during analysis.</p>
<p></p>
<p>Attributes:</p>
<p>anomaly_type: Type of anomaly detected</p>
<p>severity: Severity level ("low", "medium", "high", "critical")</p>
<p>description: Human-readable description</p>
<p>affected_modules: List of modules where anomaly was detected</p>
</div>
</details>
</li>
<li><code>SharedContext</code> (workflow_orchestrator.py)
<details><summary>Shared context passed between workflow modules.</summary>
<div class="doc-comment">
<p>Shared context passed between workflow modules.</p>
<p></p>
<p>Attributes:</p>
<p>input_data: Original input data</p>
<p>intermediate_results: Results from completed modules</p>
<p>module_status: Status tracking for each module</p>
<p>resource_usage: Resource usage statistics</p>
</div>
</details>
</li>
<li><code>ExposedService</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Represents a discovered exposed service.</span></li>
<li><code>RiskScore</code> (blockchain_analyzer.py)
<details><summary>Float-based risk score (0.0–1.0) for addresses/transactions.</summary>
<div class="doc-comment">
<p>Float-based risk score (0.0–1.0) for addresses/transactions.</p>
<p></p>
<p>Renamed from `RiskLevel` to disambiguate from canonical</p>
<p>`project_types.RiskLevel` (str-valued enum). Float semantics</p>
<p>are intentional — callers need numeric comparison, not ordinal.</p>
<p>Use `project_types.RiskLevel` for categorical risk tagging.</p>
</div>
</details>
</li>
<li><code>SearchResult</code> (academic_search.py) — <span class="doc-comment-inline">A single search result.</span></li>
<li><code>AcademicPaper</code> (open_source_collectors.py)</li>
<li><code>_RawPasteAdapter</code> (open_source_collectors.py) — <span class="doc-comment-inline">Trivial adapter: response body IS the paste text (ghostbin, rentry, pastebin_raw).</span></li>
<li><code>SSLCertificate</code> (network_reconnaissance.py) — <span class="doc-comment-inline">SSL/TLS certificate information.</span></li>
<li><code>HostInfo</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Complete host information.</span></li>
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
<li><code>Community</code> (relationship_discovery.py) — <span class="doc-comment-inline">Represents a detected community in the graph.</span></li>
<li><code>Communication</code> (relationship_discovery.py) — <span class="doc-comment-inline">Represents a communication event between entities.</span></li>
<li><code>DocumentAnalysis</code> (document_intelligence.py) — <span class="doc-comment-inline">Complete document analysis result.</span></li>
<li><code>TemporalPattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Temporal pattern with time-based characteristics.</span></li>
<li><code>FlowPattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Transaction or data flow pattern.</span></li>
<li><code>Snapshot</code> (archive_discovery.py) — <span class="doc-comment-inline">Web archive snapshot (from stealth_osint integration)</span></li>
<li><code>CourtCase</code> (open_source_collectors.py)</li>
<li><code>RecordType</code> (network_reconnaissance.py) — <span class="doc-comment-inline">DNS record types.</span></li>
<li><code>IntelligenceTarget</code> (web_intelligence.py) — <span class="doc-comment-inline">Unified intelligence target configuration.</span></li>
<li><code>Document</code> (relationship_discovery.py) — <span class="doc-comment-inline">Represents a document containing entity mentions.</span></li>
<li><code>InfluenceModel</code> (relationship_discovery.py) — <span class="doc-comment-inline">Represents influence propagation model results.</span></li>
<li><code>DocumentType</code> (document_intelligence.py) — <span class="doc-comment-inline">Supported document types.</span></li>
<li><code>LongContextAnalysis</code> (document_intelligence.py) — <span class="doc-comment-inline">Results from MLX long-context analysis.</span></li>
<li><code>BehavioralPattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Behavioral pattern from user actions.</span></li>
<li><code>CommunicationPattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Communication pattern between entities.</span></li>
<li><code>StructuralPattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Structural/organizational pattern.</span></li>
<li><code>SequentialPattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Sequential pattern from ordered events.</span></li>
<li><code>CorrelationReport</code> (workflow_orchestrator.py)
<details><summary>Report of cross-module correlations.</summary>
<div class="doc-comment">
<p>Report of cross-module correlations.</p>
<p></p>
<p>Attributes:</p>
<p>cross_module_findings: List of findings from multiple modules</p>
<p>risk_score: Calculated risk score (0.0-1.0)</p>
<p>attribution: Attribution data (e.g., threat actor, source)</p>
</div>
</details>
</li>
<li><code>WorkflowPlan</code> (workflow_orchestrator.py)
<details><summary>Plan for workflow execution.</summary>
<div class="doc-comment">
<p>Plan for workflow execution.</p>
<p></p>
<p>Attributes:</p>
<p>modules: List of module names to execute</p>
<p>execution_mode: "sequential" or "parallel"</p>
<p>parallel_groups: Optional grouping for parallel execution</p>
</div>
</details>
</li>
<li><code>ServiceType</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Types of exposed services.</span></li>
<li><code>PatternType</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Types of transaction patterns.</span></li>
<li><code>UsenetArticle</code> (open_source_collectors.py)</li>
<li><code>EdgarFiling</code> (open_source_collectors.py)</li>
<li><code>TemporalAnomaly</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detected temporal anomaly.</span></li>
<li><code>CryptanalysisResult</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Result of cryptanalysis attempt.</span></li>
<li><code>EntityType</code> (relationship_discovery.py) — <span class="doc-comment-inline">Types of entities in the relationship graph.</span></li>
<li><code>Anomaly</code> (pattern_mining.py) — <span class="doc-comment-inline">Detected anomaly in data.</span></li>
<li><code>RiskLevel</code> (exposed_service_hunter.py)
<details><summary>Risk levels for exposed services.</summary>
<div class="doc-comment">
<p>Risk levels for exposed services.</p>
<p></p>
<p>Re-ordered to canonical order (LOW → CRITICAL) to match</p>
<p>`project_types.RiskLevel`. Values are identical lowercase strings.</p>
</div>
</details>
</li>
<li><code>EntityType</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Types of entities that can be identified.</span></li>
<li><code>PasteFinding</code> (open_source_collectors.py)</li>
<li><code>ChatMessage</code> (open_source_collectors.py)</li>
<li><code>ArchiveSource</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Sources of archived content.</span></li>
<li><code>HashAnalysis</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Analysis of a hash value.</span></li>
<li><code>ExposureFinding</code> (exposure_correlator.py) — <span class="doc-comment-inline">A correlated exposure finding with evidence.</span></li>
<li><code>MetadataCategory</code> (document_intelligence.py) — <span class="doc-comment-inline">Categories of document metadata.</span></li>
<li><code>PatternType</code> (pattern_mining.py) — <span class="doc-comment-inline">Types of patterns that can be detected.</span></li>
<li><code>Transaction</code> (pattern_mining.py) — <span class="doc-comment-inline">Financial transaction for flow analysis.</span></li>
<li><code>Pattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Base pattern class.</span></li>
<li><code>PastebinResult</code> (archive_discovery.py) — <span class="doc-comment-inline">Pastebin scrape result.</span></li>
<li><code>S3Bucket</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">S3 bucket information.</span></li>
<li><code>ChainType</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Supported blockchain types.</span></li>
<li><code>AnomalyType</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Types of temporal anomalies.</span></li>
<li><code>ServiceFingerprint</code> (passive_fingerprint.py) — <span class="doc-comment-inline">A single passive service fingerprint derived from finding data.</span></li>
<li><code>TechStack</code> (passive_fingerprint.py) — <span class="doc-comment-inline">R11: Tech stack signals extracted from HTTP headers, cookies, and HTML.</span></li>
<li><code>DarkWebSource</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Types of dark web sources.</span></li>
<li><code>PGPKeyInfo</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Extracted PGP key information.</span></li>
<li><code>EncryptionDetection</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Detection of encryption type from ciphertext.</span></li>
<li><code>KeyAnalysis</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Analysis of cryptographic key.</span></li>
<li><code>EmbeddedObject</code> (document_intelligence.py) — <span class="doc-comment-inline">Represents an embedded object in a document.</span></li>
<li><code>EntityMention</code> (document_intelligence.py) — <span class="doc-comment-inline">Mention of an entity in text.</span></li>
<li><code>CrossDocumentLink</code> (document_intelligence.py) — <span class="doc-comment-inline">Link between entities across documents.</span></li>
<li><code>SeasonalityType</code> (pattern_mining.py) — <span class="doc-comment-inline">Types of seasonality patterns.</span></li>
<li><code>Action</code> (pattern_mining.py) — <span class="doc-comment-inline">User action for behavioral pattern mining.</span></li>
<li><code>Communication</code> (pattern_mining.py) — <span class="doc-comment-inline">Communication event for pattern mining.</span></li>
<li><code>ContentType</code> (archive_discovery.py) — <span class="doc-comment-inline">Types of content (from stealth_osint integration)</span></li>
<li><code>ResurrectionRequest</code> (archive_discovery.py) — <span class="doc-comment-inline">Request for content resurrection (from stealth_osint integration)</span></li>
<li><code>WaybackSnapshot</code> (archive_discovery.py) — <span class="doc-comment-inline">Structured Wayback Machine snapshot result.</span></li>
<li><code>GitHubDorkResult</code> (archive_discovery.py) — <span class="doc-comment-inline">GitHub search result.</span></li>
<li><code>CertificateInfo</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Certificate transparency information.</span></li>
<li><code>TransactionPattern</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Detected pattern in transactions.</span></li>
<li><code>Cluster</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">A cluster of related addresses.</span></li>
<li><code>SourceResult</code> (academic_search.py) — <span class="doc-comment-inline">Results from a single source.</span></li>
<li><code>ServiceBanner</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Service banner information.</span></li>
<li><code>WebIntelligenceError</code> (web_intelligence.py) — <span class="doc-comment-inline">String-based error codes for web intelligence operations.</span></li>
<li><code>OperationStatus</code> (web_intelligence.py) — <span class="doc-comment-inline">Operation status tracking.</span></li>
<li><code>EntityType</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Types of entities that can be tracked.</span></li>
<li><code>IdentityChange</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Represents an identity change event.</span></li>
<li><code>TemporalCorrelation</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Correlation between two entities across time.</span></li>
<li><code>RecoveryResult</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Result of content recovery operation.</span></li>
<li><code>CrawlTask</code> (dark_web_intelligence.py)
<details><summary>ISSUE-017: BFS crawl task — single URL with depth for parallel processing.</summary>
<div class="doc-comment">
<p>ISSUE-017: BFS crawl task — single URL with depth for parallel processing.</p>
<p>Thread-safe: immutable (frozen=True), no internal mutable state.</p>
</div>
</details>
</li>
<li><code>DHTFinding</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Structured output from DHT crawl operations.</span></li>
<li><code>TimelineEvent</code> (document_intelligence.py) — <span class="doc-comment-inline">Event extracted from document with temporal information.</span></li>
<li><code>Event</code> (pattern_mining.py) — <span class="doc-comment-inline">Generic event for pattern mining.</span></li>
<li><code>SnapshotInfo</code> (archive_discovery.py) — <span class="doc-comment-inline">Wayback snapshot information.</span></li>
<li><code>CommonCrawlSnapshot</code> (archive_discovery.py) — <span class="doc-comment-inline">Structured Common Crawl result.</span></li>
<li><code>ExposureType</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Types of exposure.</span></li>
<li><code>CrossChainResult</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Result of cross-chain analysis.</span></li>
<li><code>ResultType</code> (academic_search.py) — <span class="doc-comment-inline">Types of search results.</span></li>
<li><code>DNSRecord</code> (network_reconnaissance.py) — <span class="doc-comment-inline">DNS record information.</span></li>
<li><code>ASNInfo</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Autonomous System Number information.</span></li>
<li><code>CTRawCertificate</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Certificate Transparency log entry.</span></li>
<li><code>IntelligenceOperationType</code> (web_intelligence.py) — <span class="doc-comment-inline">Types of intelligence operations.</span></li>
<li><code>EntitySnapshot</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Snapshot of an entity at a specific point in time.</span></li>
<li><code>TemporalGap</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Represents a gap in temporal data.</span></li>
<li><code>ResolvedEntity</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Result of temporal entity resolution.</span></li>
<li><code>TlsSignals</code> (passive_fingerprint.py)</li>
<li><code>AssetSignal</code> (exposure_correlator.py) — <span class="doc-comment-inline">A single signal associated with an asset.</span></li>
<li><code>TrendDirection</code> (pattern_mining.py) — <span class="doc-comment-inline">Direction of trend in temporal patterns.</span></li>
<li><code>AnomalyType</code> (pattern_mining.py) — <span class="doc-comment-inline">Types of anomalies that can be detected.</span></li>
<li><code>CorrelationMatrix</code> (pattern_mining.py) — <span class="doc-comment-inline">Cross-pattern correlation results.</span></li>
<li><code>ContentSource</code> (archive_discovery.py) — <span class="doc-comment-inline">Sources of archived content (from stealth_osint integration)</span></li>
<li><code>TechIntelligence</code> (web_intelligence.py) — <span class="doc-comment-inline">Tech stack intelligence inferred from job postings.</span></li>
<li><code>FingerprintResult</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Outcome of a passive fingerprinting run.</span></li>
<li><code>OpenStorageResult</code> (exposure_correlator.py) — <span class="doc-comment-inline">Normalized DTO for open storage scan results.</span></li>
<li><code>Verdict</code> (dns_tunnel_detector.py) — <span class="doc-comment-inline">Detection verdict enumeration.</span></li>
<li><code>APIResponse</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Cached API response wrapper.</span></li>
<li><code>AcademicSource</code> (academic_search.py) — <span class="doc-comment-inline">Available academic sources.</span></li>
<li><code>CNAMERecord</code> (network_reconnaissance.py) — <span class="doc-comment-inline">CNAME chain record.</span></li>
<li><code>HttpSignals</code> (passive_fingerprint.py)</li>
<li><code>HtmlSignals</code> (passive_fingerprint.py)</li>
<li><code>OnionType</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Types of onion services.</span></li>
<li><code>CtSignals</code> (passive_fingerprint.py)</li>
<li><code>TemporalError</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">String-based error codes for temporal archaeology operations.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (766)</summary>
<ul>
<li><code>infer_tech_from_jobs</code> (web_intelligence.py)
<details><summary>Infer technology stack from job postings across multiple sources.</summary>
<div class="doc-comment">
<p>Infer technology stack from job postings across multiple sources.</p>
<p></p>
<p>Sources:</p>
<p>- Indeed RSS: https://www.indeed.com/rss?q={entity_name}+engineer</p>
<p>- Hacker News "Who is Hiring": HN API topstories.json filtered monthly</p>
<p>- Remoteok.com API: https://remoteok.io/api?tag={entity_name}</p>
<p></p>
<p>Args:</p>
<p>entity_name: Company/entity name to search job postings for</p>
<p></p>
<p>Returns:</p>
<p>TechIntelligence with detected_technologies, hiring_patterns,</p>
<p>seniority_distribution, and inferred_pain_points</p>
</div>
</details>
</li>
<li><code>extract_and_encode_images</code> (dark_web_intelligence.py)
<details><summary>Sprint F214R: Extract images from crawled HTML and store VisionEncoder embeddings.</summary>
<div class="doc-comment">
<p>Sprint F214R: Extract images from crawled HTML and store VisionEncoder embeddings.</p>
<p></p>
<p>Gate: HLEDAC_ENABLE_IMAGE_OSINT=1 (default: off).</p>
<p>Bounded: max 3 images per page, 512KB per image, 8s timeout.</p>
<p>Fail-soft: any exception → log warning, return [].</p>
</div>
</details>
</li>
<li><code>detect_communities</code> (relationship_discovery.py)
<details><summary>Detect communities in the relationship graph.</summary>
<div class="doc-comment">
<p>Detect communities in the relationship graph.</p>
<p></p>
<p>Args:</p>
<p>algorithm: Community detection algorithm (louvain, label_propagation)</p>
<p>resolution: Resolution parameter for Louvain algorithm</p>
<p></p>
<p>Returns:</p>
<p>List of detected communities</p>
</div>
</details>
</li>
<li><code>model_influence_propagation</code> (relationship_discovery.py)
<details><summary>Model influence propagation through the network.</summary>
<div class="doc-comment">
<p>Model influence propagation through the network.</p>
<p></p>
<p>Args:</p>
<p>seed_entities: Initial influential entities</p>
<p>iterations: Maximum iterations</p>
<p>damping: Damping factor for propagation</p>
<p>convergence_threshold: Convergence threshold</p>
<p></p>
<p>Returns:</p>
<p>InfluenceModel with propagation results</p>
</div>
</details>
</li>
<li><code>find_hidden_paths</code> (relationship_discovery.py)
<details><summary>Find hidden connection paths between two entities.</summary>
<div class="doc-comment">
<p>Find hidden connection paths between two entities.</p>
<p></p>
<p>Args:</p>
<p>entity_a: Starting entity ID</p>
<p>entity_b: Target entity ID</p>
<p>max_depth: Maximum path length</p>
<p>min_strength: Minimum relationship strength threshold</p>
<p>max_paths: Maximum number of paths to return</p>
<p></p>
<p>Returns:</p>
<p>List of connection paths</p>
</div>
</details>
</li>
<li><code>_deduplicate_and_rank</code> (academic_search.py)
<details><summary>Unified deduplication + ranking via single TaskGroup + Queue pipeline.</summary>
<div class="doc-comment">
<p>Unified deduplication + ranking via single TaskGroup + Queue pipeline.</p>
<p></p>
<p>Pass 1 (producer): builds DedupItems from SearchResults (CPU-bound hash).</p>
<p>Queue (maxsize=512): backpressure when consumer is slower than producer.</p>
<p>Pass 2 (consumer): deduplicates then ranks items pulled from queue.</p>
<p></p>
<p>Both passes run concurrently within a single TaskGroup — no GIL</p>
<p>serialization between them. CPU-bound work runs on asyncio.to_thread</p>
<p>which releases the GIL during the hash/scoring computation.</p>
</div>
</details>
</li>
<li><code>compute_match</code> (identity_stitching.py)
<details><summary>Compute match between two profiles.</summary>
<div class="doc-comment">
<p>Compute match between two profiles.</p>
<p></p>
<p>Args:</p>
<p>profile_a: First profile</p>
<p>profile_b: Second profile</p>
<p></p>
<p>Returns:</p>
<p>IdentityMatch with scores and signals</p>
</div>
</details>
</li>
<li><code>find_all_matches_async</code> (identity_stitching.py)
<details><summary>Find all matches across all profiles — MUST be called from async context.</summary>
<div class="doc-comment">
<p>Find all matches across all profiles — MUST be called from async context.</p>
<p></p>
<p>O(N²) brute-force replaced by:</p>
<p>- LSH pre-filtering: O(1) candidate reduction per profile</p>
<p>- Parallel async pairwise: bounded semaphore, concurrency=10</p>
<p>Falls back to O(N²) when LSH unavailable.</p>
</div>
</details>
</li>
<li><code>_extract_metadata_html</code> (archive_discovery.py)
<details><summary>Extract metadata from HTML content.</summary>
<div class="doc-comment">
<p>Extract metadata from HTML content.</p>
<p></p>
<p>Tier 2 migration: selectolax-first → bs4 fallback → regex/stdlib fallback.</p>
</div>
</details>
</li>
<li><code>stitch_identities</code> (identity_stitching.py)
<details><summary>Stitch identities based on matches.</summary>
<div class="doc-comment">
<p>Stitch identities based on matches.</p>
<p></p>
<p>O(α(N)) Union-Find clustering nahrazuje O(N²) connected_components.</p>
<p>Zároveň opraven bug: profile_ids → comp_profile_ids na řádku StitchedIdentity.</p>
<p></p>
<p>Args:</p>
<p>match_threshold: Threshold for direct stitching</p>
<p>transitive_threshold: Threshold for transitive stitching (unused, kept for compat)</p>
<p></p>
<p>Returns:</p>
<p>List of StitchedIdentity objects</p>
</div>
</details>
</li>
<li><code>recover_deleted_content</code> (temporal_archaeologist.py)
<details><summary>Recover deleted content from multiple archive sources.</summary>
<div class="doc-comment">
<p>Recover deleted content from multiple archive sources.</p>
<p></p>
<p>Args:</p>
<p>url: URL to recover</p>
<p>sources: List of sources to check (default: all)</p>
<p>from_date: Start date for recovery</p>
<p>to_date: End date for recovery</p>
<p>include_content: Whether to fetch full content</p>
<p></p>
<p>Returns:</p>
<p>RecoveryResult with recovered versions</p>
</div>
</details>
</li>
<li><code>_recover_from_wayback</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Wayback Machine.</span></li>
<li><code>_load_graph</code> (relationship_discovery.py)
<details><summary>Load a graph from disk.</summary>
<div class="doc-comment">
<p>Load a graph from disk.</p>
<p></p>
<p>Format policy (F-BLOOM-REGRESSION companion):</p>
<p>* Our JSON envelope (orjson + node_link) is the canonical read path.</p>
<p>No ``pickle.load`` exec surface.</p>
<p>* igraph native ``Graph.Load`` (NOT Python pickle) is used for files</p>
<p>that don't match our JSON magic.</p>
<p>* Legacy ``.pkl`` (Python pickle) is only accepted as a one-shot</p>
<p>migration and ONLY on F196B-safe paths (``~/.hledac/graphs``).</p>
<p></p>
<p>SECURITY: F196B — legacy pickle is rejected outside the application's</p>
<p>graph directory. New code never writes Python pickle.</p>
</div>
</details>
</li>
<li><code>hunt</code> (exposed_service_hunter.py)
<details><summary>Perform comprehensive exposed service hunt.</summary>
<div class="doc-comment">
<p>Perform comprehensive exposed service hunt.</p>
<p></p>
<p>Args:</p>
<p>target: Target domain or company name</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with categorized findings</p>
</div>
</details>
</li>
<li><code>_fetch_osv_batch</code> (exposure_clients.py)
<details><summary>Fetch CVEs via OSV.dev batch API.</summary>
<div class="doc-comment">
<p>Fetch CVEs via OSV.dev batch API.</p>
<p>Yields dicts with CVE data. Falls back to NVD on 0 results.</p>
</div>
</details>
</li>
<li><code>analyze_transaction_flows</code> (pattern_mining.py)
<details><summary>Analyze transaction flows for patterns.</summary>
<div class="doc-comment">
<p>Analyze transaction flows for patterns.</p>
<p></p>
<p>Args:</p>
<p>transactions: List of financial transactions</p>
<p>min_transactions: Minimum transactions required</p>
<p></p>
<p>Returns:</p>
<p>FlowPattern with transaction flow analysis</p>
</div>
</details>
</li>
<li><code>detect_wildcard</code> (network_reconnaissance.py)
<details><summary>Detect wildcard DNS configuration for a domain.</summary>
<div class="doc-comment">
<p>Detect wildcard DNS configuration for a domain.</p>
<p></p>
<p>Uses high-entropy random subdomains to probe for wildcard responses.</p>
<p>Conservative approach: returns wildcard_suspected=False on errors/ambiguity.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to check for wildcard DNS</p>
<p></p>
<p>Returns:</p>
<p>Dict with:</p>
<p>- wildcard_suspected: bool</p>
<p>- probe_count: int</p>
<p>- responses: list of probe results</p>
<p>- probe_method: str</p>
</div>
</details>
</li>
<li><code>calculate_centrality</code> (relationship_discovery.py)
<details><summary>Calculate centrality metrics for all entities.</summary>
<div class="doc-comment">
<p>Calculate centrality metrics for all entities.</p>
<p></p>
<p>Args:</p>
<p>metric: Centrality metric (betweenness, closeness, degree, eigenvector, pagerank)</p>
<p>use_mlx: Use MLX acceleration if available</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping entity IDs to centrality scores</p>
</div>
</details>
</li>
<li><code>gather_all</code> (open_source_collectors.py)
<details><summary>Gather from all or specified sources.</summary>
<div class="doc-comment">
<p>Gather from all or specified sources.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>sources: List of sources to search. If None, searches all.</p>
<p>Options: pastebin, usenet, matrix, academic, sec_edgar, court_records</p>
<p></p>
<p>Returns:</p>
<p>Dict mapping source name to list of finding dicts</p>
</div>
</details>
</li>
<li><code>load_graph</code> (relationship_discovery.py)
<details><summary>Load persisted NetworkX graph from disk with node-count bound.</summary>
<div class="doc-comment">
<p>Load persisted NetworkX graph from disk with node-count bound.</p>
<p></p>
<p>Reads JSON envelope (orjson + node_link). Legacy ``.pkl`` is accepted</p>
<p>only on F196B-safe paths as a one-shot migration.</p>
<p></p>
<p>Returns True if loaded, False if file missing or error.</p>
</div>
</details>
</li>
<li><code>analyze_massive_dump</code> (document_intelligence.py)
<details><summary>Analyze massive text dump using MLX acceleration.</summary>
<div class="doc-comment">
<p>Analyze massive text dump using MLX acceleration.</p>
<p></p>
<p>Args:</p>
<p>text: Large text to analyze (can be millions of tokens)</p>
<p>source: Source identifier</p>
<p>extract_entities: Whether to extract entities</p>
<p>build_timeline: Whether to build timeline</p>
<p>cross_reference: Whether to cross-reference entities</p>
<p></p>
<p>Returns:</p>
<p>LongContextAnalysis with all findings</p>
</div>
</details>
</li>
<li><code>execute_intelligence_operation</code> (web_intelligence.py)
<details><summary>Execute comprehensive intelligence operation on target.</summary>
<div class="doc-comment">
<p>Execute comprehensive intelligence operation on target.</p>
<p></p>
<p>Args:</p>
<p>target: Intelligence target configuration</p>
<p>operation_types: Types of operations to perform (default: all available)</p>
<p></p>
<p>Returns:</p>
<p>Operation ID for tracking results</p>
</div>
</details>
</li>
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
<li><code>analyze_image</code> (document_intelligence.py)
<details><summary>Analyze image for forensic artifacts.</summary>
<div class="doc-comment">
<p>Analyze image for forensic artifacts.</p>
<p></p>
<p>Uses ProcessPoolExecutor for CPU-bound image analysis (ELA) to avoid</p>
<p>contention with MLX workers. M1 8GB safe: max 2 workers.</p>
<p></p>
<p>Args:</p>
<p>content: Image bytes</p>
<p>url: Optional URL of the image for graph integration (S49-C)</p>
<p></p>
<p>Returns:</p>
<p>Dict with analysis results including ela_score, suspicious flag, etc.</p>
</div>
</details>
</li>
<li><code>_execute_module</code> (workflow_orchestrator.py)
<details><summary>Execute a single module.</summary>
<div class="doc-comment">
<p>Execute a single module.</p>
<p></p>
<p>Args:</p>
<p>module: Module name</p>
<p>input_data: Input data</p>
<p>context: Shared execution context</p>
<p></p>
<p>Returns:</p>
<p>Module execution result</p>
</div>
</details>
</li>
<li><code>_probe_pdf</code> (document_intelligence.py)
<details><summary>Probe PDF to estimate signal score and identify candidate pages.</summary>
<div class="doc-comment">
<p>Probe PDF to estimate signal score and identify candidate pages.</p>
<p></p>
<p>Args:</p>
<p>doc: PyMuPDF document object</p>
<p></p>
<p>Returns:</p>
<p>dict with "signal_score" (float) and "candidate_pages" (list[int])</p>
</div>
</details>
</li>
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
<li><code>crawl_onion</code> (dark_web_intelligence.py)
<details><summary>ISSUE-017: BFS crawl — bounded concurrency, Rust URL dedup.</summary>
<div class="doc-comment">
<p>ISSUE-017: BFS crawl — bounded concurrency, Rust URL dedup.</p>
<p></p>
<p>Replaces depth-first serial crawling with breadth-first parallel</p>
<p>processing using asyncio.Queue + parallel() bounded concurrency.</p>
<p></p>
<p>Pipeline: enqueue → parallel fetch → process results → enqueue new URLs</p>
<p>Rust MmapUrlSet (parking_lot::RwLock) for thread-safe URL dedup across coroutines.</p>
</div>
</details>
</li>
<li><code>_fetch_single_nvd</code> (exposure_clients.py)
<details><summary>Fetch CVEs for a single tech from NVD (rate-limited, cached).</summary>
<div class="doc-comment">
<p>Fetch CVEs for a single tech from NVD (rate-limited, cached).</p>
<p>Returns list of CVE dicts for yield.</p>
<p></p>
<p>ISSUE #016: Unified rate limiter interface — Rust NvdRateLimiter (token bucket)</p>
<p>or Python asyncio.Semaphore fallback.</p>
<p>- Rust try_acquire() non-blocking → cooperative async sleep loop</p>
<p>- Python Semaphore → async context manager</p>
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
<li><code>_extract_exif</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract EXIF data from image.</span></li>
<li><code>_execute_operation_async</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute intelligence operation asynchronously with per-host concurrency control.</span></li>
<li><code>cross_temporal_correlation</code> (temporal_archaeologist.py)
<details><summary>Find correlations between two entities across time.</summary>
<div class="doc-comment">
<p>Find correlations between two entities across time.</p>
<p></p>
<p>Args:</p>
<p>entity_a: First entity identifier</p>
<p>entity_b: Second entity identifier</p>
<p>id_type: Type of identifiers</p>
<p></p>
<p>Returns:</p>
<p>TemporalCorrelation with correlation analysis</p>
</div>
</details>
</li>
<li><code>query_host</code> (exposure_clients.py)
<details><summary>Query Shodan data pro danou IP.</summary>
<div class="doc-comment">
<p>Query Shodan data pro danou IP.</p>
<p></p>
<p>1. LMDB lookup (b"shodan:" + ip)</p>
<p>2. Cache hit → return cached data</p>
<p>3. Cache miss + SHODAN_API_KEY → HTTP GET api.shodan.io</p>
<p>4. Cache miss + no key → log INFO + return None</p>
<p></p>
<p>Returns:</p>
<p>dict s Shodan daty nebo None.</p>
</div>
</details>
</li>
<li><code>_predict_hidden_lsh</code> (relationship_discovery.py)</li>
<li><code>analyze_multiple_dumps_async</code> (document_intelligence.py)
<details><summary>Analyze multiple document dumps in parallel with optional cross-correlation.</summary>
<div class="doc-comment">
<p>Analyze multiple document dumps in parallel with optional cross-correlation.</p>
<p></p>
<p>Uses parallel() with concurrency=4 for M1-safe parallel processing.</p>
</div>
</details>
</li>
<li><code>find_sequential_patterns</code> (pattern_mining.py)
<details><summary>Find frequent sequential patterns using SPADE-like algorithm.</summary>
<div class="doc-comment">
<p>Find frequent sequential patterns using SPADE-like algorithm.</p>
<p></p>
<p>Args:</p>
<p>sequences: List of sequences (each sequence is a list of items)</p>
<p>min_support: Minimum support threshold (default: self.min_support)</p>
<p>max_pattern_length: Maximum length of patterns to find</p>
<p></p>
<p>Returns:</p>
<p>List of sequential patterns</p>
</div>
</details>
</li>
<li><code>search</code> (academic_search.py)
<details><summary>Execute multi-source academic search.</summary>
<div class="doc-comment">
<p>Execute multi-source academic search.</p>
<p></p>
<p>Args:</p>
<p>query: Original search query</p>
<p>max_results: Maximum total results to return</p>
<p>enable_expansion: Whether to expand the query (overrides default)</p>
<p>sources: List of source names to use (default: all)</p>
<p>async_session: Optional shared aiohttp session for connection pooling.</p>
<p>If provided, adapters reuse this session instead of</p>
<p>creating per-call sessions (reduces connection overhead).</p>
<p></p>
<p>Returns:</p>
<p>Academic search result</p>
</div>
</details>
</li>
<li><code>_execute_searches</code> (academic_search.py) — <span class="doc-comment-inline">Execute searches across all sources.</span></li>
<li><code>discover_from_cooccurrence</code> (relationship_discovery.py)
<details><summary>Discover relationships from entity co-occurrence in documents.</summary>
<div class="doc-comment">
<p>Discover relationships from entity co-occurrence in documents.</p>
<p></p>
<p>Args:</p>
<p>documents: List of documents containing entity mentions</p>
<p>min_cooccurrence: Minimum co-occurrences to establish relationship</p>
<p>window_size: Optional context window size for co-occurrence</p>
<p></p>
<p>Returns:</p>
<p>List of discovered relationships</p>
</div>
</details>
</li>
<li><code>analyze</code> (document_intelligence.py)
<details><summary>Analyze any supported document type.</summary>
<div class="doc-comment">
<p>Analyze any supported document type.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to document file</p>
<p></p>
<p>Returns:</p>
<p>DocumentAnalysis with all extracted intelligence</p>
</div>
</details>
</li>
<li><code>_compute_fft_periodicity</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect periodicity using FFT (O(n log n) instead of O(n²) autocorrelation).</span></li>
<li><code>search</code> (academic_search.py)
<details><summary>Search Semantic Scholar for papers.</summary>
<div class="doc-comment">
<p>Search Semantic Scholar for papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>max_results: Maximum results to return</p>
<p>analysis: Optional query analysis</p>
<p>async_session: Optional shared aiohttp session for connection pooling.</p>
<p>If not provided, creates a per-call session (legacy behavior).</p>
</div>
</details>
</li>
<li><code>search_hosts</code> (exposure_clients.py)
<details><summary>Search Censys hosts.</summary>
<div class="doc-comment">
<p>Search Censys hosts.</p>
<p></p>
<p>1. LMDB lookup (b"censys:" + query)</p>
<p>2. Cache hit → return cached data</p>
<p>3. Cache miss + API credentials → HTTP POST to Censys API v2</p>
<p>4. Cache miss + no credentials → log INFO + return None</p>
<p></p>
<p>Returns:</p>
<p>list of host results nebo None.</p>
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
<li><code>_parse_results</code> (academic_search.py) — <span class="doc-comment-inline">Parse ArXiv API XML response.</span></li>
<li><code>deep_historical_search</code> (temporal_archaeologist.py)
<details><summary>Perform deep historical search across archives.</summary>
<div class="doc-comment">
<p>Perform deep historical search across archives.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>time_range: Tuple of (start_date, end_date)</p>
<p>sources: List of sources to search</p>
<p></p>
<p>Returns:</p>
<p>List of archived versions matching query</p>
<p></p>
<p>ISSUE-003: Parallelized source search (was sequential for-loop).</p>
</div>
</details>
</li>
<li><code>prepare_training_data</code> (relationship_discovery.py) — <span class="doc-comment-inline">Prepare training data for GNN.</span></li>
<li><code>discover_from_communications</code> (relationship_discovery.py)
<details><summary>Discover relationships from communication patterns.</summary>
<div class="doc-comment">
<p>Discover relationships from communication patterns.</p>
<p></p>
<p>Args:</p>
<p>communications: List of communication events</p>
<p>min_communications: Minimum communications to establish relationship</p>
<p>time_window_days: Optional time window for analysis</p>
<p></p>
<p>Returns:</p>
<p>List of discovered relationships</p>
</div>
</details>
</li>
<li><code>_recon_domain</code> (network_reconnaissance.py)
<details><summary>Reconnaissance for domain name.</summary>
<div class="doc-comment">
<p>Reconnaissance for domain name.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to recon</p>
<p>include_subdomains: Whether to brute force subdomains (default False for passive)</p>
</div>
</details>
</li>
<li><code>_save_graph</code> (relationship_discovery.py)
<details><summary>Save the current graph to disk.</summary>
<div class="doc-comment">
<p>Save the current graph to disk.</p>
<p></p>
<p>Format policy (F-BLOOM-REGRESSION companion):</p>
<p>* igraph -&gt; ``write_picklez`` (igraph's native compact format, NOT</p>
<p>the Python ``pickle`` module — no exec surface).</p>
<p>* NetworkX -&gt; ``_graph_serde.save_nx_graph_jsonl`` (JSON via orjson,</p>
<p>zero-copy, no ``pickle`` interpreter surface).</p>
<p>* Fallback: igraph instance saved via JSON if available.</p>
<p></p>
<p>Both paths bounded, fail-soft. Never raises.</p>
</div>
</details>
</li>
<li><code>_detect_anomalies</code> (workflow_orchestrator.py)
<details><summary>Detect anomalies in module results.</summary>
<div class="doc-comment">
<p>Detect anomalies in module results.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p></p>
<p>Returns:</p>
<p>List of detected anomalies</p>
</div>
</details>
</li>
<li><code>enumerate_buckets</code> (exposed_service_hunter.py)
<details><summary>Enumerate S3 buckets using naming patterns.</summary>
<div class="doc-comment">
<p>Enumerate S3 buckets using naming patterns.</p>
<p></p>
<p>Args:</p>
<p>target: Target domain or company name</p>
<p>max_concurrent: Maximum concurrent requests</p>
<p></p>
<p>Returns:</p>
<p>List of exposed S3 buckets</p>
</div>
</details>
</li>
<li><code>calculate_risk_score</code> (blockchain_analyzer.py)
<details><summary>Calculate risk score for a wallet.</summary>
<div class="doc-comment">
<p>Calculate risk score for a wallet.</p>
<p></p>
<p>Args:</p>
<p>analysis: WalletAnalysis object</p>
<p></p>
<p>Returns:</p>
<p>Risk score between 0.0 (minimal) and 1.0 (critical)</p>
</div>
</details>
</li>
<li><code>search</code> (academic_search.py)
<details><summary>Search Crossref for academic papers.</summary>
<div class="doc-comment">
<p>Search Crossref for academic papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>max_results: Maximum results to return</p>
<p>analysis: Optional query analysis</p>
<p>async_session: Optional shared aiohttp session for connection pooling.</p>
<p>If not provided, creates a per-call session (legacy behavior).</p>
</div>
</details>
</li>
<li><code>search_arxiv</code> (academic_search.py) — <span class="doc-comment-inline">ArXiv API — security preprints. [{title, summary, published, link}]</span></li>
<li><code>_parse_content</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Parse HTML content and extract intelligence.</span></li>
<li><code>view_host</code> (exposure_clients.py)
<details><summary>View Censys host details.</summary>
<div class="doc-comment">
<p>View Censys host details.</p>
<p></p>
<p>1. LMDB lookup (censys:view:{ip})</p>
<p>2. Cache hit → return</p>
<p>3. Cache miss + API credentials → HTTP GET</p>
<p>4. Cache miss + no credentials → None</p>
</div>
</details>
</li>
<li><code>_extract_cert_info</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Extract information from certificate object.</span></li>
<li><code>add_relationship</code> (relationship_discovery.py)
<details><summary>Add a relationship between entities.</summary>
<div class="doc-comment">
<p>Add a relationship between entities.</p>
<p></p>
<p>Args:</p>
<p>relationship: Relationship to add</p>
<p></p>
<p>Returns:</p>
<p>True if added, False if already exists</p>
</div>
</details>
</li>
<li><code>find_matches</code> (identity_stitching.py)
<details><summary>Find potential matches for a profile.</summary>
<div class="doc-comment">
<p>Find potential matches for a profile.</p>
<p></p>
<p>Args:</p>
<p>profile_id: Profile ID to find matches for</p>
<p>min_score: Minimum match score (uses similarity_threshold if None)</p>
<p></p>
<p>Returns:</p>
<p>List of IdentityMatch objects sorted by score</p>
</div>
</details>
</li>
<li><code>get_snapshots</code> (archive_discovery.py)
<details><summary>Vrátí [{url, timestamp, statuscode, mimetype}] — max `limit` snapshotů.</summary>
<div class="doc-comment">
<p>Vrátí [{url, timestamp, statuscode, mimetype}] — max `limit` snapshotů.</p>
<p>Akceptuje URL i domain (auto-detekce podle wildcard syntaxe).</p>
<p>Bez externí session — vytváří vlastní.</p>
</div>
</details>
</li>
<li><code>execute_workflow</code> (workflow_orchestrator.py)
<details><summary>Execute a workflow plan.</summary>
<div class="doc-comment">
<p>Execute a workflow plan.</p>
<p></p>
<p>Args:</p>
<p>workflow: Workflow plan with module configuration</p>
<p>input_data: Input data for analysis</p>
<p></p>
<p>Returns:</p>
<p>Comprehensive analysis report</p>
</div>
</details>
</li>
<li><code>search</code> (academic_search.py)
<details><summary>Search ArXiv for papers.</summary>
<div class="doc-comment">
<p>Search ArXiv for papers.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>max_results: Maximum results to return</p>
<p>analysis: Optional query analysis</p>
<p>async_session: Optional shared aiohttp session for connection pooling.</p>
<p>If not provided, creates a per-call session (legacy behavior).</p>
</div>
</details>
</li>
<li><code>find_nodes_for_hash</code> (network_reconnaissance.py)
<details><summary>FIND_NODE query pro konkrétní info_hash.</summary>
<div class="doc-comment">
<p>FIND_NODE query pro konkrétní info_hash.</p>
<p>Vrátí list hostnames/IPs z DHT odpovědí.</p>
<p>M1: asyncio.DatagramEndpoint — čistě async UDP.</p>
</div>
</details>
</li>
<li><code>cleanup</code> (web_intelligence.py) — <span class="doc-comment-inline">Cleanup all system resources. Idempotent — safe to call multiple times.</span></li>
<li><code>search_cve</code> (exposure_clients.py)
<details><summary>Search GitHub code for CVE PoC samples.</summary>
<div class="doc-comment">
<p>Search GitHub code for CVE PoC samples.</p>
<p></p>
<p>Returns [{repo, url, path, stars}] — max 10 results.</p>
</div>
</details>
</li>
<li><code>analyze</code> (document_intelligence.py)
<details><summary>Analyze PDF document.</summary>
<div class="doc-comment">
<p>Analyze PDF document.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to PDF file, bytes, or file-like object</p>
<p></p>
<p>Returns:</p>
<p>DocumentAnalysis with all extracted data</p>
</div>
</details>
</li>
<li><code>get_identity_graph</code> (identity_stitching.py)
<details><summary>Get the identity graph with all profiles and matches.</summary>
<div class="doc-comment">
<p>Get the identity graph with all profiles and matches.</p>
<p></p>
<p>Returns:</p>
<p>igraph Graph with identity data (M1-optimized C-core)</p>
</div>
</details>
</li>
<li><code>_generate_recommendations</code> (workflow_orchestrator.py)
<details><summary>Generate actionable recommendations.</summary>
<div class="doc-comment">
<p>Generate actionable recommendations.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p>correlations: Correlation report</p>
<p>anomalies: Detected anomalies</p>
<p></p>
<p>Returns:</p>
<p>List of recommendation strings</p>
</div>
</details>
</li>
<li><code>scan_hosts</code> (exposed_service_hunter.py)
<details><summary>Scan hosts for exposed database ports.</summary>
<div class="doc-comment">
<p>Scan hosts for exposed database ports.</p>
<p></p>
<p>Args:</p>
<p>hosts: List of hostnames or IPs to scan</p>
<p>ports: Specific ports to check (default: all database ports)</p>
<p>max_concurrent: Maximum concurrent connections</p>
<p></p>
<p>Returns:</p>
<p>List of exposed database services</p>
</div>
</details>
</li>
<li><code>predict_hidden_connections</code> (relationship_discovery.py)</li>
<li><code>cross_chain_analysis</code> (blockchain_analyzer.py)
<details><summary>Perform cross-chain analysis.</summary>
<div class="doc-comment">
<p>Perform cross-chain analysis.</p>
<p></p>
<p>Args:</p>
<p>addresses: Dictionary mapping chain to address</p>
<p></p>
<p>Returns:</p>
<p>CrossChainResult with findings</p>
</div>
</details>
</li>
<li><code>_rank_results</code> (academic_search.py) — <span class="doc-comment-inline">Rank results by relevance (P1-3: parallel scoring with asyncio.to_thread).</span></li>
<li><code>_build_igraph_graph</code> (relationship_discovery.py) — <span class="doc-comment-inline">Build igraph graph (M1 optimized, preferred over networkx when available).</span></li>
<li><code>probe</code> (document_intelligence.py)
<details><summary>Probe document to estimate value score for progressive parsing.</summary>
<div class="doc-comment">
<p>Probe document to estimate value score for progressive parsing.</p>
<p></p>
<p>Args:</p>
<p>url: Document URL</p>
<p>preview_bytes: Preview content bytes (first ~256KB)</p>
<p>query: Optional search query for semantic scoring</p>
<p></p>
<p>Returns:</p>
<p>dict with heuristic_score, semantic_score (if computed), final_score, keywords, entities</p>
</div>
</details>
</li>
<li><code>_detect_periodicity_mlx</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect periodicity using MLX FFT (M1 optimized).</span></li>
<li><code>query_domain</code> (exposed_service_hunter.py)
<details><summary>Query certificate transparency logs for a domain.</summary>
<div class="doc-comment">
<p>Query certificate transparency logs for a domain.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to query</p>
<p>include_subdomains: Include wildcard subdomains</p>
<p></p>
<p>Returns:</p>
<p>List of discovered subdomains</p>
</div>
</details>
</li>
<li><code>_cluster_by_temporal_correlation</code> (blockchain_analyzer.py)
<details><summary>Cluster by temporal correlation.</summary>
<div class="doc-comment">
<p>Cluster by temporal correlation.</p>
<p></p>
<p>Addresses with similar transaction timing patterns</p>
<p>may belong to the same entity.</p>
</div>
</details>
</li>
<li><code>_parse_results</code> (academic_search.py) — <span class="doc-comment-inline">Parse Crossref API JSON response.</span></li>
<li><code>fetch_cve_intelligence</code> (exposure_clients.py)
<details><summary>Fetch CVE intelligence for a tech stack.</summary>
<div class="doc-comment">
<p>Fetch CVE intelligence for a tech stack.</p>
<p></p>
<p>1. OSV.dev Batch API (priority) with streaming</p>
<p>2. NVD API 2.0 fallback (if OSV returns 0)</p>
<p>3. EPSS score enrichment per CVE</p>
<p></p>
<p>Yields dicts with CVE data + EPSS enrichment.</p>
<p>EPSS &gt;0.7 flags CVE as IMMEDIATE_ACTION.</p>
<p></p>
<p>Memory bounded: max 200 CVEs, batches of 20.</p>
<p>LMDB cache: 6h TTL for CVE data.</p>
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
<li><code>_predict_hidden_brute_force</code> (relationship_discovery.py)</li>
<li><code>_check_wayback</code> (archive_discovery.py) — <span class="doc-comment-inline">Check Wayback Machine CDX API for snapshots</span></li>
<li><code>search_ss</code> (academic_search.py) — <span class="doc-comment-inline">Semantic Scholar: [{title, abstract, year, doi, authors}]</span></li>
<li><code>reconstruct_version_history</code> (temporal_archaeologist.py)
<details><summary>Reconstruct version history for an entity.</summary>
<div class="doc-comment">
<p>Reconstruct version history for an entity.</p>
<p></p>
<p>Args:</p>
<p>identifier: Entity identifier (URL, username, etc.)</p>
<p>id_type: Type of identifier (url, username, email, etc.)</p>
<p>from_date: Start date for reconstruction</p>
<p>to_date: End date for reconstruction</p>
<p></p>
<p>Returns:</p>
<p>EntityTimeline with reconstructed history</p>
</div>
</details>
</li>
<li><code>detect_temporal_anomalies</code> (temporal_archaeologist.py)
<details><summary>Detect temporal anomalies in a timeline.</summary>
<div class="doc-comment">
<p>Detect temporal anomalies in a timeline.</p>
<p></p>
<p>Args:</p>
<p>timeline: EntityTimeline to analyze</p>
<p></p>
<p>Returns:</p>
<p>List of detected anomalies</p>
</div>
</details>
</li>
<li><code>_crawl_task</code> (dark_web_intelligence.py)
<details><summary>ISSUE-017: Process single crawl task — fetch + extract links + enqueue new URLs.</summary>
<div class="doc-comment">
<p>ISSUE-017: Process single crawl task — fetch + extract links + enqueue new URLs.</p>
<p>Thread-safe: uses Rust MmapUrlSet (or OrderedDict fallback) for dedup.</p>
</div>
</details>
</li>
<li><code>_fetch_page</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Fetch a single page through Tor.</span></li>
<li><code>monitor_service</code> (dark_web_intelligence.py)
<details><summary>Continuously monitor a hidden service for changes.</summary>
<div class="doc-comment">
<p>Continuously monitor a hidden service for changes.</p>
<p></p>
<p>Args:</p>
<p>onion_address: .onion address to monitor</p>
<p>interval_minutes: Check interval in minutes</p>
<p></p>
<p>Yields:</p>
<p>Change notifications</p>
<p></p>
<p>Note:</p>
<p>Bounded by caller's iteration — caller MUST use ``async for``</p>
<p>or ``try/finally`` with ``aclose()`` to ensure cleanup on cancel.</p>
<p>``asyncio.CancelledError`` propagates from ``aclose()`` into the</p>
<p>``await asyncio.sleep()`` call, causing immediate loop termination.</p>
</div>
</details>
</li>
<li><code>_enrich_batch_epss</code> (exposure_clients.py)
<details><summary>ISSUE-003: Parallelize EPSS enrichment for a batch of CVEs.</summary>
<div class="doc-comment">
<p>ISSUE-003: Parallelize EPSS enrichment for a batch of CVEs.</p>
<p>Replaces sequential `await _enrich_epss` per CVE with parallel().</p>
<p>Returns list of CVEs with EPSS fields populated.</p>
</div>
</details>
</li>
<li><code>search_url</code> (archive_discovery.py)
<details><summary>Search for archived versions of a URL.</summary>
<div class="doc-comment">
<p>Search for archived versions of a URL.</p>
<p></p>
<p>Args:</p>
<p>url: URL to search</p>
<p>sources: List of sources (wayback, archive_today, etc.)</p>
<p>limit_per_source: Maximum results per source</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of source -&gt; results</p>
</div>
</details>
</li>
<li><code>_analyze_ethereum_wallet</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Analyze Ethereum wallet using Etherscan.</span></li>
<li><code>_cluster_by_amount_patterns</code> (blockchain_analyzer.py)
<details><summary>Cluster by similar amount patterns.</summary>
<div class="doc-comment">
<p>Cluster by similar amount patterns.</p>
<p></p>
<p>Addresses with similar transaction amount distributions</p>
<p>may belong to the same entity.</p>
</div>
</details>
</li>
<li><code>_parse_results</code> (academic_search.py) — <span class="doc-comment-inline">Parse Semantic Scholar API JSON response.</span></li>
<li><code>_filter_private_ips_batch</code> (network_reconnaissance.py)
<details><summary>Batch-filter IPs using Rust batch_ip_classify.</summary>
<div class="doc-comment">
<p>Batch-filter IPs using Rust batch_ip_classify.</p>
<p></p>
<p>Returns (public_ips, private_ips) based on Rust classification.</p>
<p>Falls back to Python _is_private_ip if Rust unavailable.</p>
<p></p>
<p>Rust IpClass: 0=invalid, 1=private, 2=public, 3=loopback, 4=link-local</p>
<p>Private = class in (1, 3, 4) — Rust does same checks as Python _is_private_ip.</p>
</div>
</details>
</li>
<li><code>__init__</code> (relationship_discovery.py)
<details><summary>Initialize the Relationship Discovery Engine.</summary>
<div class="doc-comment">
<p>Initialize the Relationship Discovery Engine.</p>
<p></p>
<p>Args:</p>
<p>use_sparse: Use scipy.sparse for large graphs (memory efficient)</p>
<p>max_memory_mb: ADVISORY ceiling in MB — not hard-enforced.</p>
<p>512MB recommended for M1 8GB UMA; 1024 is too aggressive.</p>
<p>enable_mlx: Enable MLX acceleration where available</p>
<p>lazy_evaluation: Defer expensive computations until needed</p>
</div>
</details>
</li>
<li><code>analyze</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze image file.</span></li>
<li><code>_extract_gps</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract GPS coordinates from EXIF.</span></li>
<li><code>mine_behavioral_patterns</code> (pattern_mining.py)
<details><summary>Mine behavioral patterns from user actions.</summary>
<div class="doc-comment">
<p>Mine behavioral patterns from user actions.</p>
<p></p>
<p>Args:</p>
<p>actions: List of user actions</p>
<p>min_actions: Minimum actions per user required</p>
<p></p>
<p>Returns:</p>
<p>List of detected behavioral patterns</p>
</div>
</details>
</li>
<li><code>compute_style_similarity</code> (identity_stitching.py)
<details><summary>Compute writing style similarity between two sets of texts.</summary>
<div class="doc-comment">
<p>Compute writing style similarity between two sets of texts.</p>
<p></p>
<p>Uses TF-IDF cosine similarity if sklearn is available,</p>
<p>falls back to simple lexical similarity.</p>
<p></p>
<p>Args:</p>
<p>texts1: First set of texts</p>
<p>texts2: Second set of texts</p>
<p></p>
<p>Returns:</p>
<p>Similarity score (0-1)</p>
</div>
</details>
</li>
<li><code>discover_endpoints</code> (exposed_service_hunter.py)
<details><summary>Discover GraphQL endpoints on a target.</summary>
<div class="doc-comment">
<p>Discover GraphQL endpoints on a target.</p>
<p></p>
<p>Args:</p>
<p>base_url: Base URL to scan</p>
<p>max_concurrent: Maximum concurrent requests</p>
<p></p>
<p>Returns:</p>
<p>List of discovered GraphQL endpoints</p>
</div>
</details>
</li>
<li><code>trace_transactions</code> (blockchain_analyzer.py)
<details><summary>Trace transaction chains from an address.</summary>
<div class="doc-comment">
<p>Trace transaction chains from an address.</p>
<p></p>
<p>Args:</p>
<p>address: Starting address</p>
<p>chain: Blockchain type</p>
<p>depth: How many hops to trace</p>
<p>max_transactions: Maximum transactions to return</p>
<p></p>
<p>Returns:</p>
<p>List of Transaction objects</p>
</div>
</details>
</li>
<li><code>analyze_certificate</code> (network_reconnaissance.py)
<details><summary>Analyze SSL certificate of remote host.</summary>
<div class="doc-comment">
<p>Analyze SSL certificate of remote host.</p>
<p></p>
<p>Args:</p>
<p>hostname: Host to connect to</p>
<p>port: Port (default 443)</p>
<p></p>
<p>Returns:</p>
<p>SSLCertificate or None</p>
</div>
</details>
</li>
<li><code>__init__</code> (web_intelligence.py)</li>
<li><code>_group_similar_snapshots</code> (temporal_archaeologist.py)
<details><summary>Group similar snapshots using clustering.</summary>
<div class="doc-comment">
<p>Group similar snapshots using clustering.</p>
<p></p>
<p>ISSUE-026 FIX #3: Uses Rust rayon-parallel trigram Jaccard grouping</p>
<p>(text_similarity::group_similar_texts) when available — ~10-50× faster</p>
<p>than the serial Python SequenceMatcher O(n²) approach for large batches.</p>
<p>Falls back to pure-Python implementation if Rust extension unavailable.</p>
</div>
</details>
</li>
<li><code>_extract_links</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Extract .onion links from content.</span></li>
<li><code>find_cliques</code> (relationship_discovery.py)
<details><summary>Find cliques in the relationship graph.</summary>
<div class="doc-comment">
<p>Find cliques in the relationship graph.</p>
<p></p>
<p>Args:</p>
<p>min_size: Minimum clique size</p>
<p></p>
<p>Returns:</p>
<p>List of cliques (each clique is a list of entity IDs)</p>
</div>
</details>
</li>
<li><code>_ela_analysis_mps_sync</code> (document_intelligence.py) — <span class="doc-comment-inline">Synchronous MPS implementation of ELA.</span></li>
<li><code>_compute_semantic_score</code> (document_intelligence.py) — <span class="doc-comment-inline">Compute semantic similarity score between text and query using ModernBERT.</span></li>
<li><code>mine_temporal_patterns</code> (pattern_mining.py)
<details><summary>Mine temporal patterns from events.</summary>
<div class="doc-comment">
<p>Mine temporal patterns from events.</p>
<p></p>
<p>Args:</p>
<p>events: List of events with timestamps</p>
<p>min_events: Minimum number of events required</p>
<p></p>
<p>Returns:</p>
<p>List of detected temporal patterns</p>
</div>
</details>
</li>
<li><code>__init__</code> (identity_stitching.py)
<details><summary>Initialize the Identity Stitching Engine.</summary>
<div class="doc-comment">
<p>Initialize the Identity Stitching Engine.</p>
<p></p>
<p>Args:</p>
<p>similarity_threshold: Minimum similarity score for matching</p>
<p>signal_weights: Custom weights for match signals (uses defaults if None)</p>
<p>max_memory_mb: ADVISORY ceiling in MB — not hard-enforced.</p>
<p>Default 512MB is appropriate for M1 8GB UMA.</p>
<p>enable_fuzzy: Enable fuzzy string matching (requires rapidfuzz)</p>
</div>
</details>
</li>
<li><code>compute_username_similarity</code> (identity_stitching.py)
<details><summary>Compute similarity between two usernames.</summary>
<div class="doc-comment">
<p>Compute similarity between two usernames.</p>
<p></p>
<p>Uses rapidfuzz for fast fuzzy matching if available,</p>
<p>falls back to simple normalized comparison.</p>
<p></p>
<p>Args:</p>
<p>user1: First username</p>
<p>user2: Second username</p>
<p></p>
<p>Returns:</p>
<p>Similarity score (0-1)</p>
</div>
</details>
</li>
<li><code>_correlate_results</code> (workflow_orchestrator.py)
<details><summary>Correlate results across modules.</summary>
<div class="doc-comment">
<p>Correlate results across modules.</p>
<p></p>
<p>Args:</p>
<p>results: Dictionary of module results</p>
<p></p>
<p>Returns:</p>
<p>Correlation report with findings and risk score</p>
</div>
</details>
</li>
<li><code>detect_patterns</code> (blockchain_analyzer.py)
<details><summary>Detect suspicious patterns in transactions.</summary>
<div class="doc-comment">
<p>Detect suspicious patterns in transactions.</p>
<p></p>
<p>Args:</p>
<p>transactions: List of transactions to analyze</p>
<p></p>
<p>Returns:</p>
<p>List of detected TransactionPattern objects</p>
</div>
</details>
</li>
<li><code>brute_force_subdomains</code> (network_reconnaissance.py)
<details><summary>Brute force subdomains.</summary>
<div class="doc-comment">
<p>Brute force subdomains.</p>
<p></p>
<p>Returns:</p>
<p>List of (subdomain, ip, record_type) tuples</p>
</div>
</details>
</li>
<li><code>classify_ip</code> (exposure_clients.py) — <span class="doc-comment-inline">Vrátí {"ip", "classification", "name", "link", "noise", "riot"}</span></li>
<li><code>crack_dictionary</code> (cryptographic_intelligence.py)
<details><summary>Attempt dictionary attack on hash.</summary>
<div class="doc-comment">
<p>Attempt dictionary attack on hash.</p>
<p></p>
<p>Args:</p>
<p>hash_value: Hash to crack</p>
<p>wordlist: List of passwords to try (uses common passwords if None)</p>
<p>hash_type: Known hash type (auto-detect if None)</p>
<p></p>
<p>Returns:</p>
<p>Cracked password or None</p>
</div>
</details>
</li>
<li><code>affinity_analysis</code> (relationship_discovery.py)
<details><summary>Perform affinity analysis on entities.</summary>
<div class="doc-comment">
<p>Perform affinity analysis on entities.</p>
<p></p>
<p>Args:</p>
<p>entity_type: Filter by entity type (None for all)</p>
<p>metric: Affinity metric (cooccurrence, jaccard, cosine)</p>
<p>use_mlx: Use MLX acceleration for similarity computation</p>
<p></p>
<p>Returns:</p>
<p>AffinityMatrix containing similarity scores</p>
</div>
</details>
</li>
<li><code>find_similar_chunks_mlx</code> (document_intelligence.py)
<details><summary>Find most similar chunks to query using MLX.</summary>
<div class="doc-comment">
<p>Find most similar chunks to query using MLX.</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>top_k: Number of results to return</p>
<p></p>
<p>Returns:</p>
<p>List of (chunk_index, similarity_score) tuples</p>
</div>
</details>
</li>
<li><code>detect_change_points</code> (pattern_mining.py)
<details><summary>Detect change points in time series using wavelet + Mamba2 (with fallbacks).</summary>
<div class="doc-comment">
<p>Detect change points in time series using wavelet + Mamba2 (with fallbacks).</p>
<p></p>
<p>Uses:</p>
<p>1. Wavelet decomposition for change detection</p>
<p>2. Mamba2 forecasting for anomaly detection (best-effort)</p>
<p>3. EWMA/CUSUM fallbacks if MLX unavailable</p>
<p></p>
<p>Args:</p>
<p>series: Time series data</p>
<p></p>
<p>Returns:</p>
<p>List of change point indices</p>
</div>
</details>
</li>
<li><code>resurrect</code> (archive_discovery.py) — <span class="doc-comment-inline">Resurrect content from web archives.</span></li>
<li><code>_rate_limited_etherscan</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Make rate-limited Etherscan API call.</span></li>
<li><code>_rate_limited_blockchair</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Make rate-limited Blockchair API call.</span></li>
<li><code>query_hash</code> (exposure_clients.py)
<details><summary>Query MalwareBazaar for file hash intelligence.</summary>
<div class="doc-comment">
<p>Query MalwareBazaar for file hash intelligence.</p>
<p></p>
<p>Returns raw MB response dict with query_status and data.</p>
</div>
</details>
</li>
<li><code>analyze</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze image content for steganography using semaphore pool.</span></li>
<li><code>_detect_trend</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect trend in event values or frequency.</span></li>
<li><code>mine_communication_patterns</code> (pattern_mining.py)
<details><summary>Mine communication patterns.</summary>
<div class="doc-comment">
<p>Mine communication patterns.</p>
<p></p>
<p>Args:</p>
<p>communications: List of communication events</p>
<p>min_communications: Minimum communications required</p>
<p></p>
<p>Returns:</p>
<p>List of detected communication patterns</p>
</div>
</details>
</li>
<li><code>find_all_matches</code> (identity_stitching.py)
<details><summary>Find all matches across all profiles — sync wrapper for CLI entry points.</summary>
<div class="doc-comment">
<p>Find all matches across all profiles — sync wrapper for CLI entry points.</p>
<p></p>
<p>ISSUE-005 FIX: Replaces asyncio.run() with asyncio.get_running_loop().run_until_complete()</p>
<p>which is safe on Python 3.14+ when called from a non-running-loop async context.</p>
<p>The try/except RuntimeError pattern above is retained for explicit error messaging</p>
<p>when called incorrectly from an active event loop.</p>
</div>
</details>
</li>
<li><code>__init__</code> (blockchain_analyzer.py)
<details><summary>Initialize BlockchainForensics.</summary>
<div class="doc-comment">
<p>Initialize BlockchainForensics.</p>
<p></p>
<p>Args:</p>
<p>etherscan_api_key: API key for Etherscan (Ethereum)</p>
<p>blockchair_api_key: API key for Blockchair (Bitcoin, others)</p>
<p>cache_ttl_seconds: Cache time-to-live in seconds (default: 300)</p>
<p>max_concurrent_requests: Max concurrent API requests (default: 5)</p>
<p>fetch_func: Optional async fetch function(url: str) -&gt; dict.</p>
<p>When provided, takes precedence over internal httpx client.</p>
<p>Enables canonical transport seam (circuit breaker, shared session).</p>
</div>
</details>
</li>
<li><code>identify_hash</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Identify possible hash types from hash string.</span></li>
<li><code>_build_adjacency_matrix</code> (relationship_discovery.py) — <span class="doc-comment-inline">Build adjacency matrix (sparse or dense).</span></li>
<li><code>_calculate_centrality_igraph</code> (relationship_discovery.py) — <span class="doc-comment-inline">Calculate centrality using igraph (M1 optimized).</span></li>
<li><code>_split_preview_into_chunks</code> (document_intelligence.py)
<details><summary>Split preview bytes into chunks for embedding.</summary>
<div class="doc-comment">
<p>Split preview bytes into chunks for embedding.</p>
<p></p>
<p>Args:</p>
<p>bytes_data: Preview bytes</p>
<p>max_chunks: Maximum number of chunks</p>
<p>max_tokens: Maximum tokens per chunk (approximated by word count)</p>
<p></p>
<p>Returns:</p>
<p>List of text chunks</p>
</div>
</details>
</li>
<li><code>chunk_text</code> (document_intelligence.py)
<details><summary>Split text into overlapping chunks with metadata.</summary>
<div class="doc-comment">
<p>Split text into overlapping chunks with metadata.</p>
<p></p>
<p>Args:</p>
<p>text: Large text to chunk</p>
<p>source: Source identifier (filename, URL, etc.)</p>
<p></p>
<p>Returns:</p>
<p>List of chunks with metadata</p>
</div>
</details>
</li>
<li><code>_detect_periodicity_autocorr</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect periodicity using autocorrelation.</span></li>
<li><code>_detect_bursts</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect burst patterns in event timing.</span></li>
<li><code>batch_pattern_matching</code> (pattern_mining.py)
<details><summary>Match patterns against data in batches (M1 memory optimized).</summary>
<div class="doc-comment">
<p>Match patterns against data in batches (M1 memory optimized).</p>
<p></p>
<p>Args:</p>
<p>patterns: Patterns to match</p>
<p>data_batch: Data to match against</p>
<p>batch_size: Size of processing batches</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping data index to matched patterns</p>
</div>
</details>
</li>
<li><code>get_file_history</code> (archive_discovery.py) — <span class="doc-comment-inline">Get historical versions of a file from GitHub.</span></li>
<li><code>_execute_parallel</code> (workflow_orchestrator.py)
<details><summary>Execute modules in parallel groups.</summary>
<div class="doc-comment">
<p>Execute modules in parallel groups.</p>
<p></p>
<p>Args:</p>
<p>module_groups: Groups of modules to execute in parallel</p>
<p>input_data: Input data</p>
<p>context: Shared execution context</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of module results</p>
</div>
</details>
</li>
<li><code>_get_module_instance</code> (workflow_orchestrator.py)
<details><summary>Get module instance from registry or orchestrator.</summary>
<div class="doc-comment">
<p>Get module instance from registry or orchestrator.</p>
<p></p>
<p>Args:</p>
<p>module: Module name</p>
<p></p>
<p>Returns:</p>
<p>Module instance or None</p>
</div>
</details>
</li>
<li><code>_check_port</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if a specific port is open and identify service.</span></li>
<li><code>enumerate_all</code> (network_reconnaissance.py)
<details><summary>Comprehensive DNS enumeration.</summary>
<div class="doc-comment">
<p>Comprehensive DNS enumeration.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to enumerate</p>
<p>include_subdomains: Whether to brute force subdomains</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with all DNS findings</p>
</div>
</details>
</li>
<li><code>permutation_scan</code> (network_reconnaissance.py)
<details><summary>Scan for subdomains using permutations.</summary>
<div class="doc-comment">
<p>Scan for subdomains using permutations.</p>
<p></p>
<p>Combines words with separators to find non-standard subdomains.</p>
</div>
</details>
</li>
<li><code>_parse_certificate</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Parse DER certificate.</span></li>
<li><code>_age_queued_priorities</code> (web_intelligence.py)
<details><summary>Age queued operations to improve priority over time.</summary>
<div class="doc-comment">
<p>Age queued operations to improve priority over time.</p>
<p></p>
<p>HARD EXIT: waits on shutdown event so task terminates immediately on cleanup.</p>
</div>
</details>
</li>
<li><code>compute_embeddings_mlx</code> (document_intelligence.py)
<details><summary>Compute MLX embeddings for chunks.</summary>
<div class="doc-comment">
<p>Compute MLX embeddings for chunks.</p>
<p></p>
<p>Args:</p>
<p>chunks: List of text chunks</p>
<p></p>
<p>Returns:</p>
<p>MLX array of embeddings or None if MLX unavailable</p>
</div>
</details>
</li>
<li><code>analyze_multiple_dumps</code> (document_intelligence.py)
<details><summary>Analyze multiple document dumps and optionally cross-correlate (sync wrapper).</summary>
<div class="doc-comment">
<p>Analyze multiple document dumps and optionally cross-correlate (sync wrapper).</p>
<p></p>
<p>Args:</p>
<p>dumps: Dict of {source_name: text_content}</p>
<p>cross_correlate: Whether to find links between dumps</p>
<p></p>
<p>Returns:</p>
<p>Dict of analyses per dump</p>
</div>
</details>
</li>
<li><code>search_across_dumps_async</code> (document_intelligence.py)
<details><summary>Search for query across multiple dumps using MLX similarity (parallel).</summary>
<div class="doc-comment">
<p>Search for query across multiple dumps using MLX similarity (parallel).</p>
<p></p>
<p>Uses parallel() with concurrency=4 for M1-safe parallel processing.</p>
</div>
</details>
</li>
<li><code>to_markdown</code> (workflow_orchestrator.py)
<details><summary>Export report as Markdown string.</summary>
<div class="doc-comment">
<p>Export report as Markdown string.</p>
<p></p>
<p>Returns:</p>
<p>Markdown formatted report</p>
</div>
</details>
</li>
<li><code>test_mongodb_auth</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Test MongoDB for authentication requirements.</span></li>
<li><code>cluster_addresses</code> (blockchain_analyzer.py)
<details><summary>Cluster addresses using heuristics.</summary>
<div class="doc-comment">
<p>Cluster addresses using heuristics.</p>
<p></p>
<p>Args:</p>
<p>addresses: List of addresses to cluster</p>
<p>chain: Blockchain type</p>
<p></p>
<p>Returns:</p>
<p>List of Cluster objects</p>
</div>
</details>
</li>
<li><code>_deduplicate_results</code> (academic_search.py) — <span class="doc-comment-inline">Deduplicate results using deduplication engine.</span></li>
<li><code>new_identity</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Request new Tor identity (new exit node).</span></li>
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
<li><code>predict_with_gnn</code> (relationship_discovery.py)
<details><summary>Použije GNN k predikci skrytých spojení.</summary>
<div class="doc-comment">
<p>Použije GNN k predikci skrytých spojení.</p>
<p></p>
<p>Args:</p>
<p>max_predictions: Maximální počet predikcí</p>
<p></p>
<p>Returns:</p>
<p>Seznam tuple (source_id, target_id, score)</p>
</div>
</details>
</li>
<li><code>get_network_stats</code> (relationship_discovery.py) — <span class="doc-comment-inline">Get comprehensive network statistics.</span></li>
<li><code>_extract_ooxml_core_props</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract core properties from OOXML.</span></li>
<li><code>compute_temporal_overlap</code> (identity_stitching.py)
<details><summary>Compute temporal overlap between two activity timelines.</summary>
<div class="doc-comment">
<p>Compute temporal overlap between two activity timelines.</p>
<p></p>
<p>Args:</p>
<p>activity1: First activity timeline</p>
<p>activity2: Second activity timeline</p>
<p>window_days: Time window for considering overlap</p>
<p></p>
<p>Returns:</p>
<p>Overlap score (0-1)</p>
</div>
</details>
</li>
<li><code>get_snapshots</code> (archive_discovery.py) — <span class="doc-comment-inline">Get list of snapshots for a URL.</span></li>
<li><code>_check_endpoint</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if a URL is a GraphQL endpoint with introspection enabled.</span></li>
<li><code>_cluster_by_common_input</code> (blockchain_analyzer.py)
<details><summary>Cluster by common input ownership.</summary>
<div class="doc-comment">
<p>Cluster by common input ownership.</p>
<p></p>
<p>If two addresses appear as inputs to the same transaction,</p>
<p>they likely belong to the same entity.</p>
</div>
</details>
</li>
<li><code>_execute_web_scraping</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute web scraping operations.</span></li>
<li><code>_enrich_epss</code> (exposure_clients.py)
<details><summary>Fetch EPSS score for a CVE.</summary>
<div class="doc-comment">
<p>Fetch EPSS score for a CVE.</p>
<p>Returns {"epss_score": float, "percentile": float} or None.</p>
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
<li><code>batch_analyze_async</code> (document_intelligence.py)
<details><summary>Analyze multiple documents in parallel (M1-safe, concurrency=8).</summary>
<div class="doc-comment">
<p>Analyze multiple documents in parallel (M1-safe, concurrency=8).</p>
<p></p>
<p>Uses parallel() with policy='collect' — all documents processed,</p>
<p>individual failures return None for that document without aborting others.</p>
</div>
</details>
</li>
<li><code>cross_reference_entities</code> (document_intelligence.py)
<details><summary>Find entities that appear across multiple documents.</summary>
<div class="doc-comment">
<p>Find entities that appear across multiple documents.</p>
<p></p>
<p>Args:</p>
<p>all_entities: All entities extracted from all documents</p>
<p></p>
<p>Returns:</p>
<p>List of cross-document links</p>
</div>
</details>
</li>
<li><code>reconstruct_timeline</code> (document_intelligence.py)
<details><summary>Reconstruct timeline from temporal entities.</summary>
<div class="doc-comment">
<p>Reconstruct timeline from temporal entities.</p>
<p></p>
<p>Args:</p>
<p>entities: Extracted entities</p>
<p>chunks: Document chunks</p>
<p></p>
<p>Returns:</p>
<p>List of timeline events</p>
</div>
</details>
</li>
<li><code>_detect_temporal_anomalies</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect anomalies in temporal pattern.</span></li>
<li><code>to_entities_and_relationships</code> (identity_stitching.py)
<details><summary>Convert stitched identities to Entity and Relationship objects.</summary>
<div class="doc-comment">
<p>Convert stitched identities to Entity and Relationship objects.</p>
<p></p>
<p>Args:</p>
<p>stitched_identities: Pre-computed stitched identities (optional)</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (entities, relationships) for RelationshipDiscoveryEngine</p>
</div>
</details>
</li>
<li><code>to_html</code> (workflow_orchestrator.py)
<details><summary>Export report as HTML string.</summary>
<div class="doc-comment">
<p>Export report as HTML string.</p>
<p></p>
<p>Returns:</p>
<p>HTML formatted report</p>
</div>
</details>
</li>
<li><code>_check_bucket_exists</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if an S3 bucket exists and is accessible.</span></li>
<li><code>scan_docker_apis</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Scan for exposed Docker APIs.</span></li>
<li><code>scan_kubernetes_apis</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Scan for exposed Kubernetes APIs.</span></li>
<li><code>analyze_wallet</code> (blockchain_analyzer.py)
<details><summary>Perform comprehensive wallet analysis.</summary>
<div class="doc-comment">
<p>Perform comprehensive wallet analysis.</p>
<p></p>
<p>Args:</p>
<p>address: Wallet address to analyze</p>
<p>chain: Blockchain type (ethereum, bitcoin, etc.)</p>
<p></p>
<p>Returns:</p>
<p>WalletAnalysis with comprehensive metrics</p>
</div>
</details>
</li>
<li><code>_extract_host</code> (web_intelligence.py)
<details><summary>Extract the primary host from a target's URLs.</summary>
<div class="doc-comment">
<p>Extract the primary host from a target's URLs.</p>
<p></p>
<p>Used by per-host gate to rate-limit concurrent operations per domain.</p>
<p></p>
<p>Args:</p>
<p>target: IntelligenceTarget with urls list</p>
<p></p>
<p>Returns:</p>
<p>Host string (e.g. "example.com") or empty string if no valid URL</p>
</div>
</details>
</li>
<li><code>temporal_entity_resolution</code> (temporal_archaeologist.py)
<details><summary>Resolve entity identity across multiple snapshots.</summary>
<div class="doc-comment">
<p>Resolve entity identity across multiple snapshots.</p>
<p></p>
<p>Args:</p>
<p>snapshots: List of archived versions</p>
<p>resolution_threshold: Minimum similarity for identity matching</p>
<p></p>
<p>Returns:</p>
<p>ResolvedEntity with canonical identity</p>
</div>
</details>
</li>
<li><code>crawl_onion_legacy</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Legacy depth-first crawl (kept for backward compatibility).</span></li>
<li><code>get</code> (exposure_clients.py)
<details><summary>Synchroní LMDB get. Vrací cached data nebo None.</summary>
<div class="doc-comment">
<p>Synchroní LMDB get. Vrací cached data nebo None.</p>
<p>Kontroluje TTL.</p>
</div>
</details>
</li>
<li><code>rail_fence_decrypt</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Decrypt Rail Fence cipher.</span></li>
<li><code>auto_crack</code> (cryptographic_intelligence.py)
<details><summary>Automatically try to crack unknown classical cipher.</summary>
<div class="doc-comment">
<p>Automatically try to crack unknown classical cipher.</p>
<p></p>
<p>Tries multiple methods and returns best result.</p>
</div>
</details>
</li>
<li><code>analyze_security</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Analyze certificate security.</span></li>
<li><code>_deep_parse_pages</code> (document_intelligence.py)
<details><summary>Deep parse specific pages of the PDF.</summary>
<div class="doc-comment">
<p>Deep parse specific pages of the PDF.</p>
<p></p>
<p>Args:</p>
<p>doc: PyMuPDF document object</p>
<p>page_indices: List of page indices to parse</p>
<p></p>
<p>Returns:</p>
<p>List of extracted text strings for each page</p>
</div>
</details>
</li>
<li><code>_compute_heuristic_score</code> (document_intelligence.py) — <span class="doc-comment-inline">Compute heuristic value score based on content analysis.</span></li>
<li><code>_extract_snapshot</code> (archive_discovery.py) — <span class="doc-comment-inline">Extract content from a single snapshot</span></li>
<li><code>search</code> (archive_discovery.py)
<details><summary>Search GitHub code using advanced operators.</summary>
<div class="doc-comment">
<p>Search GitHub code using advanced operators.</p>
<p>Example: "leaked password" language:python extension:env</p>
</div>
</details>
</li>
<li><code>test_redis_auth</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Test Redis for authentication requirements.</span></li>
<li><code>_detect_frequency_shifts</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect shifts in update frequency.</span></li>
<li><code>_crawl_single_onion</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Crawl a single onion address and return results list (for parallel()).</span></li>
<li><code>analyze</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Analyze data to detect encryption.</span></li>
<li><code>predict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Predict hidden relationships.</span></li>
<li><code>search_across_dumps</code> (document_intelligence.py)
<details><summary>Search for query across multiple dumps using MLX similarity (sync wrapper).</summary>
<div class="doc-comment">
<p>Search for query across multiple dumps using MLX similarity (sync wrapper).</p>
<p></p>
<p>Args:</p>
<p>query: Search query</p>
<p>dumps: Dict of {source_name: text_content}</p>
<p>top_k_per_dump: Number of results per dump</p>
<p></p>
<p>Returns:</p>
<p>Dict of search results per dump</p>
</div>
</details>
</li>
<li><code>_detect_seasonality</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect daily/weekly seasonality patterns.</span></li>
<li><code>_maybe_evict_on_pressure</code> (identity_stitching.py) — <span class="doc-comment-inline">Evict 50% of cache if RSS exceeds memory pressure threshold.</span></li>
<li><code>_execute_sequential</code> (workflow_orchestrator.py)
<details><summary>Execute modules sequentially.</summary>
<div class="doc-comment">
<p>Execute modules sequentially.</p>
<p></p>
<p>Args:</p>
<p>modules: List of module names</p>
<p>input_data: Input data</p>
<p>context: Shared execution context</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of module results</p>
</div>
</details>
</li>
<li><code>_generate_report</code> (workflow_orchestrator.py)
<details><summary>Generate comprehensive report.</summary>
<div class="doc-comment">
<p>Generate comprehensive report.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p>correlations: Correlation report</p>
<p>anomalies: Detected anomalies</p>
<p>context: Shared execution context</p>
<p></p>
<p>Returns:</p>
<p>Comprehensive analysis report</p>
</div>
</details>
</li>
<li><code>_check_kubernetes_api</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if a Kubernetes API is exposed.</span></li>
<li><code>__init__</code> (temporal_archaeologist.py)
<details><summary>Initialize TemporalArchaeologist.</summary>
<div class="doc-comment">
<p>Initialize TemporalArchaeologist.</p>
<p></p>
<p>Args:</p>
<p>max_concurrent_requests: Maximum concurrent archive requests</p>
<p>request_timeout: Timeout for archive requests in seconds</p>
<p>cache_enabled: Whether to cache results</p>
<p>max_content_size_mb: Maximum content size to process in MB</p>
</div>
</details>
</li>
<li><code>_recover_from_archive_today</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Archive.today.</span></li>
<li><code>initialize</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Initialize Tor proxy connection.</span></li>
<li><code>add_entity</code> (relationship_discovery.py)
<details><summary>Add an entity to the engine.</summary>
<div class="doc-comment">
<p>Add an entity to the engine.</p>
<p></p>
<p>Args:</p>
<p>entity: Entity to add</p>
<p></p>
<p>Returns:</p>
<p>True if added, False if already exists</p>
</div>
</details>
</li>
<li><code>_analyze_ooxml</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze Office Open XML format (docx, xlsx, pptx).</span></li>
<li><code>add_profile</code> (identity_stitching.py)
<details><summary>Add an identity profile to the engine.</summary>
<div class="doc-comment">
<p>Add an identity profile to the engine.</p>
<p></p>
<p>Args:</p>
<p>profile: IdentityProfile to add</p>
<p></p>
<p>Returns:</p>
<p>True if added, False if already exists</p>
</div>
</details>
</li>
<li><code>remove_profile</code> (identity_stitching.py) — <span class="doc-comment-inline">Remove a profile and all its indexes.</span></li>
<li><code>get_snapshot_content</code> (archive_discovery.py) — <span class="doc-comment-inline">Get content of a specific snapshot.</span></li>
<li><code>__init__</code> (exposed_service_hunter.py)
<details><summary>Initialize API cache.</summary>
<div class="doc-comment">
<p>Initialize API cache.</p>
<p></p>
<p>Args:</p>
<p>cache_dir: Directory for cache DB (default: temp)</p>
<p>ttl_seconds: Cache TTL in seconds (default: 1 hour)</p>
</div>
</details>
</li>
<li><code>get</code> (exposed_service_hunter.py)
<details><summary>Get cached value if not expired.</summary>
<div class="doc-comment">
<p>Get cached value if not expired.</p>
<p></p>
<p>Args:</p>
<p>key: Cache key</p>
<p></p>
<p>Returns:</p>
<p>Cached value or None if expired/missing</p>
</div>
</details>
</li>
<li><code>_cached_request</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Make a cached API request. F184F: LRU eviction when cache exceeds MAX_CACHE_SIZE.</span></li>
<li><code>_detect_peel_chain</code> (blockchain_analyzer.py)
<details><summary>Detect peel chain pattern.</summary>
<div class="doc-comment">
<p>Detect peel chain pattern.</p>
<p></p>
<p>A peel chain is a series of transactions where:</p>
<p>1. A large amount is sent</p>
<p>2. Change is returned to a new address</p>
<p>3. Process repeats</p>
</div>
</details>
</li>
<li><code>identify_known_services</code> (blockchain_analyzer.py)
<details><summary>Identify known services associated with an address.</summary>
<div class="doc-comment">
<p>Identify known services associated with an address.</p>
<p></p>
<p>Args:</p>
<p>address: Wallet address</p>
<p></p>
<p>Returns:</p>
<p>List of service tags</p>
</div>
</details>
</li>
<li><code>get_citations</code> (academic_search.py) — <span class="doc-comment-inline">Get papers that cite this paper.</span></li>
<li><code>_initialize_components</code> (web_intelligence.py) — <span class="doc-comment-inline">Initialize all intelligence components.</span></li>
<li><code>set</code> (exposure_clients.py)
<details><summary>Synchroní LMDB set. Vrací True při úspěchu.</summary>
<div class="doc-comment">
<p>Synchroní LMDB set. Vrací True při úspěchu.</p>
<p>Single-writer přes DB_EXECUTOR.</p>
</div>
</details>
</li>
<li><code>extract_iocs</code> (exposure_clients.py)
<details><summary>Extract IOCs from MalwareBazaar response.</summary>
<div class="doc-comment">
<p>Extract IOCs from MalwareBazaar response.</p>
<p></p>
<p>Returns [(value, ioc_type)] tuples including:</p>
<p>- sha256, md5, sha1 hashes</p>
<p>- imphash</p>
<p>- malware family tags</p>
<p>- C2 IPs from vendor_intel</p>
</div>
</details>
</li>
<li><code>_mlx_similarity_matrix</code> (relationship_discovery.py) — <span class="doc-comment-inline">Compute similarity matrix using MLX acceleration.</span></li>
<li><code>_extract_frequency_pattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Extract frequency-based behavioral pattern.</span></li>
<li><code>detect_anomalies_in_pattern</code> (pattern_mining.py)
<details><summary>Detect anomalies relative to an established pattern.</summary>
<div class="doc-comment">
<p>Detect anomalies relative to an established pattern.</p>
<p></p>
<p>Args:</p>
<p>pattern: Established pattern to compare against</p>
<p>new_data: New data points to check</p>
<p>threshold: Standard deviation threshold for anomaly detection</p>
<p></p>
<p>Returns:</p>
<p>List of detected anomalies</p>
</div>
</details>
</li>
<li><code>_detect_behavioral_anomalies</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect anomalies in behavioral pattern.</span></li>
<li><code>cross_pattern_correlation</code> (pattern_mining.py)
<details><summary>Calculate correlations between patterns.</summary>
<div class="doc-comment">
<p>Calculate correlations between patterns.</p>
<p></p>
<p>Args:</p>
<p>patterns: List of patterns to correlate</p>
<p>use_mlx: Whether to use MLX acceleration</p>
<p></p>
<p>Returns:</p>
<p>CorrelationMatrix with pairwise correlations</p>
</div>
</details>
</li>
<li><code>get_paper_details</code> (academic_search.py) — <span class="doc-comment-inline">Get detailed information about a specific paper.</span></li>
<li><code>lookup</code> (network_reconnaissance.py)
<details><summary>Perform WHOIS lookup.</summary>
<div class="doc-comment">
<p>Perform WHOIS lookup.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to lookup</p>
<p></p>
<p>Returns:</p>
<p>WHOISData or None if lookup fails</p>
</div>
</details>
</li>
<li><code>memory_posture</code> (web_intelligence.py) — <span class="doc-comment-inline">Read-only seam: memory pressure state for M1 8GB.</span></li>
<li><code>_recover_from_git_history</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Git history.</span></li>
<li><code>reset_session</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Clear all session state (bounded structures + queues).</span></li>
<li><code>_fetch_nvd_fallback</code> (exposure_clients.py)
<details><summary>NVD API 2.0 fallback - parallelized with bounded concurrency.</summary>
<div class="doc-comment">
<p>NVD API 2.0 fallback - parallelized with bounded concurrency.</p>
<p></p>
<p>ISSUE-003: Replaced sequential `for tech in tech_stack` with parallel().</p>
<p>Yields CVEs as they complete (not in order) for better UX.</p>
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
<li><code>_numpy_similarity_matrix</code> (relationship_discovery.py) — <span class="doc-comment-inline">Compute similarity matrix using NumPy.</span></li>
<li><code>_extract_pdf_objects</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract embedded objects from PDF.</span></li>
<li><code>_ensure_stegdetect</code> (document_intelligence.py) — <span class="doc-comment-inline">Compile and install stegdetect if missing.</span></li>
<li><code>_detect_flow_anomalies</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect anomalies in flow pattern.</span></li>
<li><code>_extract_pattern_features</code> (pattern_mining.py) — <span class="doc-comment-inline">Extract numerical features from patterns for correlation.</span></li>
<li><code>compute_network_overlap</code> (identity_stitching.py)
<details><summary>Compute network overlap (shared connections).</summary>
<div class="doc-comment">
<p>Compute network overlap (shared connections).</p>
<p></p>
<p>Args:</p>
<p>network1: First network (set of connection IDs)</p>
<p>network2: Second network (set of connection IDs)</p>
<p></p>
<p>Returns:</p>
<p>Overlap score (0-1)</p>
</div>
</details>
</li>
<li><code>get_certificate_details</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Get detailed certificate information from CT logs.</span></li>
<li><code>get_paper_details</code> (academic_search.py) — <span class="doc-comment-inline">Get detailed information about a specific paper.</span></li>
<li><code>get_work_by_doi</code> (academic_search.py) — <span class="doc-comment-inline">Get detailed information about a work by DOI.</span></li>
<li><code>attempt_zone_transfer</code> (network_reconnaissance.py)
<details><summary>Attempt DNS zone transfer (AXFR).</summary>
<div class="doc-comment">
<p>Attempt DNS zone transfer (AXFR).</p>
<p></p>
<p>Returns:</p>
<p>List of zone records if successful, None otherwise</p>
</div>
</details>
</li>
<li><code>_ensure_components_initialized</code> (web_intelligence.py)
<details><summary>Lazy initialization — spustí komponenty a aging task pouze jednou při první operaci.</summary>
<div class="doc-comment">
<p>Lazy initialization — spustí komponenty a aging task pouze jednou při první operaci.</p>
<p></p>
<p>Uses lock to prevent race condition when multiple operations race to init.</p>
</div>
</details>
</li>
<li><code>_get_spacy_matcher</code> (web_intelligence.py) — <span class="doc-comment-inline">Lazy spaCy PhraseMatcher initialization.</span></li>
<li><code>_recover_from_google_cache</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Google Cache.</span></li>
<li><code>_recover_from_bing_cache</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Bing Cache via jina.ai.</span></li>
<li><code>_map_ecosystem</code> (exposure_clients.py)
<details><summary>Map package name/tech stack entry to (ecosystem, package_name).</summary>
<div class="doc-comment">
<p>Map package name/tech stack entry to (ecosystem, package_name).</p>
<p>Returns (ecosystem, package_name) tuple.</p>
</div>
</details>
</li>
<li><code>_score_english</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Score how likely text is English (0-1).</span></li>
<li><code>get_relationships</code> (relationship_discovery.py)
<details><summary>Get relationships, optionally filtered by entity or type.</summary>
<div class="doc-comment">
<p>Get relationships, optionally filtered by entity or type.</p>
<p></p>
<p>Args:</p>
<p>entity_id: Filter by source entity</p>
<p>relationship_type: Filter by relationship type</p>
<p></p>
<p>Returns:</p>
<p>List of matching relationships</p>
</div>
</details>
</li>
<li><code>_build_networkx_graph</code> (relationship_discovery.py) — <span class="doc-comment-inline">Build NetworkX graph (lazy evaluation).</span></li>
<li><code>predict_hidden_connections_fast</code> (relationship_discovery.py)
<details><summary>DEPRECATED: Use predict_hidden_connections(method='fast') instead.</summary>
<div class="doc-comment">
<p>DEPRECATED: Use predict_hidden_connections(method='fast') instead.</p>
<p></p>
<p>Args:</p>
<p>max_predictions: Maximum number of predictions to return</p>
<p></p>
<p>Returns:</p>
<p>List of (source_id, target_id, score) tuples sorted by score desc.</p>
</div>
</details>
</li>
<li><code>_build_entity_vectors</code> (relationship_discovery.py) — <span class="doc-comment-inline">Build feature vectors for entities based on their relationships.</span></li>
<li><code>save_graph</code> (relationship_discovery.py)
<details><summary>Persist NetworkX graph to disk with node-count pruning.</summary>
<div class="doc-comment">
<p>Persist NetworkX graph to disk with node-count pruning.</p>
<p></p>
<p>Uses ``_graph_serde.save_nx_graph_jsonl`` (JSON via orjson, no</p>
<p>Python ``pickle``). Bounded, fail-soft.</p>
</div>
</details>
</li>
<li><code>_parse_gps</code> (document_intelligence.py) — <span class="doc-comment-inline">Parse GPS data from EXIF.</span></li>
<li><code>close</code> (document_intelligence.py)
<details><summary>Close all resources including thread pool and stegdetect processes.</summary>
<div class="doc-comment">
<p>Close all resources including thread pool and stegdetect processes.</p>
<p></p>
<p>Called synchronously from __del__ (GC context) — no async allowed.</p>
<p>Stegdetect processes are killed outright (no restart needed on shutdown).</p>
</div>
</details>
</li>
<li><code>extract_entities</code> (document_intelligence.py)
<details><summary>Extract entities from text using pattern matching.</summary>
<div class="doc-comment">
<p>Extract entities from text using pattern matching.</p>
<p></p>
<p>Args:</p>
<p>text: Text to analyze</p>
<p>source: Source document</p>
<p>chunk_id: Chunk identifier</p>
<p></p>
<p>Returns:</p>
<p>List of extracted entities</p>
</div>
</details>
</li>
<li><code>__init__</code> (pattern_mining.py)
<details><summary>Initialize pattern mining engine.</summary>
<div class="doc-comment">
<p>Initialize pattern mining engine.</p>
<p></p>
<p>Args:</p>
<p>max_memory_mb: ADVISORY ceiling in MB for M1 8GB UMA (512 recommended).</p>
<p>Not hard-enforced — rely on specific bounded structures.</p>
<p>use_mlx: Whether to use MLX acceleration on M1</p>
<p>min_support: Minimum support threshold for patterns (0-1)</p>
<p>min_confidence: Minimum confidence threshold for patterns (0-1)</p>
</div>
</details>
</li>
<li><code>_extract_action_sequence</code> (pattern_mining.py) — <span class="doc-comment-inline">Extract common action sequences using sequential pattern mining.</span></li>
<li><code>_analyze_network_structure</code> (pattern_mining.py) — <span class="doc-comment-inline">Analyze overall network structure.</span></li>
<li><code>_correlation_mlx</code> (pattern_mining.py) — <span class="doc-comment-inline">Calculate correlation using MLX (M1 optimized).</span></li>
<li><code>get_identity_communities</code> (identity_stitching.py)
<details><summary>Detect communities in the identity graph.</summary>
<div class="doc-comment">
<p>Detect communities in the identity graph.</p>
<p></p>
<p>Returns:</p>
<p>List of communities (sets of profile IDs) using igraph C-core</p>
</div>
</details>
</li>
<li><code>get_recent_pastes</code> (archive_discovery.py) — <span class="doc-comment-inline">Fetch recent public pastes.</span></li>
<li><code>to_json</code> (workflow_orchestrator.py)
<details><summary>Export report as JSON string.</summary>
<div class="doc-comment">
<p>Export report as JSON string.</p>
<p></p>
<p>Returns:</p>
<p>JSON formatted report string</p>
</div>
</details>
</li>
<li><code>_check_docker_api</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check if a Docker API is exposed.</span></li>
<li><code>scan_container_apis</code> (exposed_service_hunter.py)
<details><summary>Scan for exposed Docker and Kubernetes APIs.</summary>
<div class="doc-comment">
<p>Scan for exposed Docker and Kubernetes APIs.</p>
<p></p>
<p>Args:</p>
<p>hosts: List of hostnames or IPs</p>
<p></p>
<p>Returns:</p>
<p>List of exposed container APIs</p>
</div>
</details>
</li>
<li><code>_analyze_bitcoin_wallet</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Analyze Bitcoin wallet using Blockchair.</span></li>
<li><code>_detect_mixing_patterns</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Detect potential mixing/tumbling patterns.</span></li>
<li><code>_merge_clusters</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Merge overlapping clusters.</span></li>
<li><code>_simple_deduplicate</code> (academic_search.py) — <span class="doc-comment-inline">Simple deduplication based on URL and title.</span></li>
<li><code>_process_next_queued_operation</code> (web_intelligence.py) — <span class="doc-comment-inline">Process the next queued operation after current one completes.</span></li>
<li><code>_execute_threat_assessment</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute threat assessment.</span></li>
<li><code>encode_decode</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Encode/decode various encodings.</span></li>
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
<li><code>flag_manipulated_image</code> (relationship_discovery.py)
<details><summary>S49-C: Flag manipulated image in graph and reduce credibility.</summary>
<div class="doc-comment">
<p>S49-C: Flag manipulated image in graph and reduce credibility.</p>
<p></p>
<p>Args:</p>
<p>url: URL of the manipulated image</p>
<p>ela_score: ELA score (0-1, higher = more likely manipulated)</p>
</div>
</details>
</li>
<li><code>_build_lsh_fingerprint</code> (identity_stitching.py) — <span class="doc-comment-inline">Build 64-bit SimHash fingerprint pro LSH candidate pre-filtering.</span></li>
<li><code>_check_pattern</code> (workflow_orchestrator.py)
<details><summary>Check if a pattern exists in results.</summary>
<div class="doc-comment">
<p>Check if a pattern exists in results.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p>pattern: Pattern to check (module, indicator)</p>
<p></p>
<p>Returns:</p>
<p>True if pattern detected</p>
</div>
</details>
</li>
<li><code>_extract_indicators</code> (workflow_orchestrator.py)
<details><summary>Extract suspicious indicators from results.</summary>
<div class="doc-comment">
<p>Extract suspicious indicators from results.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p></p>
<p>Returns:</p>
<p>List of indicator strings</p>
</div>
</details>
</li>
<li><code>_extract_attribution</code> (workflow_orchestrator.py)
<details><summary>Extract attribution information from results.</summary>
<div class="doc-comment">
<p>Extract attribution information from results.</p>
<p></p>
<p>Args:</p>
<p>results: Module results</p>
<p></p>
<p>Returns:</p>
<p>Attribution dictionary</p>
</div>
</details>
</li>
<li><code>_get_verdict</code> (workflow_orchestrator.py)
<details><summary>Determine verdict based on risk score.</summary>
<div class="doc-comment">
<p>Determine verdict based on risk score.</p>
<p></p>
<p>Args:</p>
<p>risk_score: Calculated risk score (0.0-1.0)</p>
<p></p>
<p>Returns:</p>
<p>Verdict string ("CLEAN", "SUSPICIOUS", or "HIGH_RISK")</p>
</div>
</details>
</li>
<li><code>execute_search</code> (academic_search.py) — <span class="doc-comment-inline">Execute search with performance tracking.</span></li>
<li><code>_execute_operation_type</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute specific operation type.</span></li>
<li><code>_check_snapshot_available</code> (temporal_archaeologist.py)
<details><summary>Check if a Wayback snapshot is available via HEAD request (Fix 1).</summary>
<div class="doc-comment">
<p>Check if a Wayback snapshot is available via HEAD request (Fix 1).</p>
<p></p>
<p>Args:</p>
<p>wayback_url: URL to check</p>
<p></p>
<p>Returns:</p>
<p>True if snapshot is available (status 200)</p>
</div>
</details>
</li>
<li><code>_detect_activity_gaps</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect unusual gaps in activity.</span></li>
<li><code>_detect_temporal_gaps</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect temporal gaps in snapshots.</span></li>
<li><code>_crawl_depth_parallel</code> (dark_web_intelligence.py)
<details><summary>ISSUE-003: Parallelize crawling of multiple links at the same depth.</summary>
<div class="doc-comment">
<p>ISSUE-003: Parallelize crawling of multiple links at the same depth.</p>
<p>Uses bounded concurrency (max 3 concurrent Tor requests) for rate safety.</p>
</div>
</details>
</li>
<li><code>vigenere_crack</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Crack Vigenere cipher using Kasiski examination and frequency analysis.</span></li>
<li><code>remove_entity</code> (relationship_discovery.py) — <span class="doc-comment-inline">Remove an entity and all its relationships.</span></li>
<li><code>_extract_pdf_metadata</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract PDF metadata.</span></li>
<li><code>_parse_pdf_date</code> (document_intelligence.py) — <span class="doc-comment-inline">Parse PDF date string format.</span></li>
<li><code>_analyze_communication_pair</code> (pattern_mining.py) — <span class="doc-comment-inline">Analyze communication pattern between a specific pair.</span></li>
<li><code>detect_periodicity_mlx</code> (pattern_mining.py)
<details><summary>Detect periodicity using MLX FFT (public API).</summary>
<div class="doc-comment">
<p>Detect periodicity using MLX FFT (public API).</p>
<p></p>
<p>Args:</p>
<p>timestamps: List of timestamps</p>
<p>values: Optional values associated with timestamps</p>
<p></p>
<p>Returns:</p>
<p>List of detected temporal patterns with periodicity</p>
</div>
</details>
</li>
<li><code>_assess_quality</code> (archive_discovery.py) — <span class="doc-comment-inline">Assess content quality (0.0-1.0)</span></li>
<li><code>check_graphql_introspection</code> (exposed_service_hunter.py)
<details><summary>Check GraphQL endpoint for introspection.</summary>
<div class="doc-comment">
<p>Check GraphQL endpoint for introspection.</p>
<p></p>
<p>Args:</p>
<p>endpoint: GraphQL endpoint URL</p>
<p></p>
<p>Returns:</p>
<p>Introspection result or None</p>
</div>
</details>
</li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>_query_whois_server</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Query specific WHOIS server.</span></li>
<li><code>recon_target</code> (network_reconnaissance.py)
<details><summary>Perform complete reconnaissance on target.</summary>
<div class="doc-comment">
<p>Perform complete reconnaissance on target.</p>
<p></p>
<p>Args:</p>
<p>target: Domain or IP address</p>
<p>include_subdomains: Whether to brute force subdomains (default False for passive)</p>
<p></p>
<p>Returns:</p>
<p>HostInfo with all gathered intelligence</p>
</div>
</details>
</li>
<li><code>pivot_domain</code> (network_reconnaissance.py)
<details><summary>Domain → IPs → buffer to IOC graph.</summary>
<div class="doc-comment">
<p>Domain → IPs → buffer to IOC graph.</p>
<p></p>
<p>Returns count of new IOCs buffered.</p>
</div>
</details>
</li>
<li><code>__init__</code> (dark_web_intelligence.py)</li>
<li><code>caesar_bruteforce</code> (cryptographic_intelligence.py)
<details><summary>Brute-force all 25 Caesar shifts and score results.</summary>
<div class="doc-comment">
<p>Brute-force all 25 Caesar shifts and score results.</p>
<p></p>
<p>Returns ranked list of possible solutions.</p>
</div>
</details>
</li>
<li><code>_calculate_entropy</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Calculate Shannon entropy of string.</span></li>
<li><code>_detect_charset</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Detect character set used in hash.</span></li>
<li><code>_guess_cipher</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Guess possible cipher type.</span></li>
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
<li><code>export_for_visualization</code> (relationship_discovery.py) — <span class="doc-comment-inline">Export graph data optimized for visualization.</span></li>
<li><code>_basic_pdf_analysis</code> (document_intelligence.py) — <span class="doc-comment-inline">Fallback basic analysis without PyMuPDF.</span></li>
<li><code>_parse_core_xml</code> (document_intelligence.py) — <span class="doc-comment-inline">Parse core.xml properties.</span></li>
<li><code>_run_async</code> (document_intelligence.py)
<details><summary>Run an async coroutine in a separate thread with its own event loop.</summary>
<div class="doc-comment">
<p>Run an async coroutine in a separate thread with its own event loop.</p>
<p></p>
<p>This avoids asyncio.run() crash on M1 and prevents blocking MLX workers.</p>
</div>
</details>
</li>
<li><code>_ingest_pattern</code> (pattern_mining.py)
<details><summary>Ingest a pattern for heavy hitters tracking.</summary>
<div class="doc-comment">
<p>Ingest a pattern for heavy hitters tracking.</p>
<p></p>
<p>Args:</p>
<p>pattern_id: Unique identifier for the pattern</p>
</div>
</details>
</li>
<li><code>search</code> (archive_discovery.py) — <span class="doc-comment-inline">Search for archived versions on Archive.today.</span></li>
<li><code>fetch_content</code> (archive_discovery.py) — <span class="doc-comment-inline">Fetch content from IPFS by CID.</span></li>
<li><code>initialize</code> (archive_discovery.py) — <span class="doc-comment-inline">Initialize security components and HTTP session</span></li>
<li><code>_detect_content_type</code> (archive_discovery.py) — <span class="doc-comment-inline">Detect content type from MIME type</span></li>
<li><code>filter_by_keyword</code> (archive_discovery.py)
<details><summary>Fetch recent pastes and filter by keyword.</summary>
<div class="doc-comment">
<p>Fetch recent pastes and filter by keyword.</p>
<p>Used for credential/component leak detection.</p>
</div>
</details>
</li>
<li><code>_fetch_bitcoin_transactions</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Fetch Bitcoin transactions from Blockchair.</span></li>
<li><code>query_records</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Query specific DNS record type.</span></li>
<li><code>_execute_osint_collection</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute OSINT collection operations.</span></li>
<li><code>_normalize_seniority</code> (web_intelligence.py) — <span class="doc-comment-inline">Infer seniority distribution from job posting text.</span></li>
<li><code>_find_overlapping_periods</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Find overlapping time periods between two timelines.</span></li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>vigenere_decrypt</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Decrypt Vigenere cipher with given key.</span></li>
<li><code>enumerate_cloud_buckets</code> (exposure_correlator.py)
<details><summary>Enumerate S3/GCP/Azure buckets for an entity name.</summary>
<div class="doc-comment">
<p>Enumerate S3/GCP/Azure buckets for an entity name.</p>
<p></p>
<p>Uses lazy generator with semaphore(10) for parallel HEAD checks.</p>
<p>Returns list of bucket findings with provider, status, and severity.</p>
<p></p>
<p>Bounds:</p>
<p>- MAX_BUCKET_CANDIDATES_PER_ENTITY=30 candidates max</p>
<p>- MAX_BUCKET_CHECKS_PARALLEL=10 parallel checks</p>
<p>- 200 = OPEN BUCKET (HIGH severity), 403 = bucket exists (MEDIUM)</p>
</div>
</details>
</li>
<li><code>detect_subdomain_takeovers</code> (exposure_correlator.py)
<details><summary>Detect subdomain takeover vulnerabilities.</summary>
<div class="doc-comment">
<p>Detect subdomain takeover vulnerabilities.</p>
<p></p>
<p>Uses PassiveDNSResolver to follow CNAME chains and identifies</p>
<p>subdomains pointing to takeover-vulnerable providers.</p>
<p></p>
<p>Returns list of takeover findings with severity=CRITICAL.</p>
<p></p>
<p>Bounds:</p>
<p>- MAX_SUBDOMAIN_TAKEOVER_SUBDOMAINS=50 subdomains per entity</p>
</div>
</details>
</li>
<li><code>enable_gnn</code> (relationship_discovery.py)
<details><summary>Inicializuje GNN prediktor a spustí trénink na pozadí, pokud je graf dostatečně velký.</summary>
<div class="doc-comment">
<p>Inicializuje GNN prediktor a spustí trénink na pozadí, pokud je graf dostatečně velký.</p>
<p></p>
<p>Args:</p>
<p>scheduler: Volitelný scheduler pro background training</p>
</div>
</details>
</li>
<li><code>_adamic_adar</code> (relationship_discovery.py) — <span class="doc-comment-inline">Compute Adamic/Adar score for non-adjacent vertices.</span></li>
<li><code>_merge_foca_metadata</code> (document_intelligence.py)
<details><summary>Merge FOCA metadata into DocumentAnalysis return value.</summary>
<div class="doc-comment">
<p>Merge FOCA metadata into DocumentAnalysis return value.</p>
<p></p>
<p>FOCA data goes into metadata.raw_metadata['foca'] — different seam from TriageFacets.</p>
</div>
</details>
</li>
<li><code>__init__</code> (document_intelligence.py)
<details><summary>Initialize MLX Long-Context Analyzer.</summary>
<div class="doc-comment">
<p>Initialize MLX Long-Context Analyzer.</p>
<p></p>
<p>Args:</p>
<p>chunk_size: Tokens per chunk (default 4096 for M1 8GB)</p>
<p>overlap: Overlap between chunks for context continuity</p>
</div>
</details>
</li>
<li><code>add</code> (pattern_mining.py) — <span class="doc-comment-inline">Add item to window.</span></li>
<li><code>_matches_pattern</code> (pattern_mining.py) — <span class="doc-comment-inline">Check if item matches pattern (simplified).</span></li>
<li><code>_index_profile_fields</code> (identity_stitching.py) — <span class="doc-comment-inline">Index username/email/alias/platform fields into reverse maps. Idempotent.</span></li>
<li><code>_check_social_archive</code> (archive_discovery.py) — <span class="doc-comment-inline">Check social media archives</span></li>
<li><code>fetch_snapshot_text</code> (archive_discovery.py)
<details><summary>Stáhnout text konkrétního snapshotu pro PatternMatcher scan.</summary>
<div class="doc-comment">
<p>Stáhnout text konkrétního snapshotu pro PatternMatcher scan.</p>
<p>URL format: https://web.archive.org/web/{timestamp}/{original_url}</p>
</div>
</details>
</li>
<li><code>_detect_round_amounts</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Detect round amount patterns (common in exchange withdrawals).</span></li>
<li><code>_is_private_ip</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Check if IP is private/reserved using ipaddress module (not regex).</span></li>
<li><code>bootstrap_nodes</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Resolve bootstrap nodes přes DNS.</span></li>
<li><code>_execute_vulnerability_analysis</code> (web_intelligence.py) — <span class="doc-comment-inline">Execute vulnerability analysis.</span></li>
<li><code>_fetch_wayback_content</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Fetch content from Wayback Machine URL.</span></li>
<li><code>_osv_to_cve</code> (exposure_clients.py) — <span class="doc-comment-inline">Convert OSV vulnerability format to our CVE dict.</span></li>
<li><code>_chi_square_score</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Calculate chi-square statistic against English frequencies.</span></li>
<li><code>_estimate_complexity</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Estimate cracking complexity.</span></li>
<li><code>to_dict</code> (academic_discovery.py) — <span class="doc-comment-inline">Convert to dictionary.</span></li>
<li><code>cleanup</code> (dns_tunnel_detector.py)
<details><summary>Clean up detector resources.</summary>
<div class="doc-comment">
<p>Clean up detector resources.</p>
<p></p>
<p>Releases memory used by the LSTM model and clears caches.</p>
</div>
</details>
</li>
<li><code>_mlx_batch_centrality</code> (relationship_discovery.py) — <span class="doc-comment-inline">Apply MLX acceleration to centrality scores.</span></li>
<li><code>_classify_path_type</code> (relationship_discovery.py) — <span class="doc-comment-inline">Classify the type of path based on relationships.</span></li>
<li><code>_get_foca_extractor</code> (document_intelligence.py) — <span class="doc-comment-inline">Lazily initialize FOCA metadata extractor (M1-safe async).</span></li>
<li><code>_analyze_ooxml_async</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze OOXML with FOCA metadata enrichment.</span></li>
<li><code>_ela_analysis_cpu_sync</code> (document_intelligence.py) — <span class="doc-comment-inline">Synchronous CPU implementation of ELA.</span></li>
<li><code>restart</code> (document_intelligence.py) — <span class="doc-comment-inline">Restart all stegdetect processes.</span></li>
<li><code>_detect_cycles</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect cycles in flow graph (simplified).</span></li>
<li><code>union</code> (identity_stitching.py)</li>
<li><code>_lexical_similarity</code> (identity_stitching.py) — <span class="doc-comment-inline">Compute lexical similarity based on word overlap.</span></li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>_find_snapshots</code> (archive_discovery.py) — <span class="doc-comment-inline">Find all available snapshots for URL</span></li>
<li><code>_check_search_cache</code> (archive_discovery.py) — <span class="doc-comment-inline">Check search engine cache for URL</span></li>
<li><code>check_bucket_permissions</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Check specific permissions on an S3 bucket.</span></li>
<li><code>introspect_endpoint</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Perform full introspection on a GraphQL endpoint.</span></li>
<li><code>enumerate_s3_buckets</code> (exposed_service_hunter.py)
<details><summary>Enumerate S3 buckets for a target.</summary>
<div class="doc-comment">
<p>Enumerate S3 buckets for a target.</p>
<p></p>
<p>Args:</p>
<p>target: Target domain or company name</p>
<p></p>
<p>Returns:</p>
<p>List of exposed S3 buckets</p>
</div>
</details>
</li>
<li><code>query_certificate_transparency</code> (exposed_service_hunter.py)
<details><summary>Query certificate transparency logs.</summary>
<div class="doc-comment">
<p>Query certificate transparency logs.</p>
<p></p>
<p>Args:</p>
<p>domain: Domain to query</p>
<p></p>
<p>Returns:</p>
<p>List of discovered subdomains</p>
</div>
</details>
</li>
<li><code>discover_graphql_endpoints</code> (exposed_service_hunter.py)
<details><summary>Discover GraphQL endpoints on a target.</summary>
<div class="doc-comment">
<p>Discover GraphQL endpoints on a target.</p>
<p></p>
<p>Args:</p>
<p>base_url: Base URL to scan</p>
<p></p>
<p>Returns:</p>
<p>List of discovered GraphQL endpoints</p>
</div>
</details>
</li>
<li><code>_detect_layering</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Detect layering pattern (multiple hops to obscure trail).</span></li>
<li><code>_calculate_correlation</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Calculate Pearson correlation coefficient.</span></li>
<li><code>_add_completed_operation</code> (web_intelligence.py)
<details><summary>Add operation to completed_operations with bounded FIFO eviction.</summary>
<div class="doc-comment">
<p>Add operation to completed_operations with bounded FIFO eviction.</p>
<p></p>
<p>Eviction policy: oldest (first-inserted) entries are removed</p>
<p>when the limit is exceeded.</p>
</div>
</details>
</li>
<li><code>_fetch_archive_today_content</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Fetch content from Archive.today.</span></li>
<li><code>_detect_content_wipes</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect sudden content wipes.</span></li>
<li><code>search_onion_addresses</code> (dark_web_intelligence.py)
<details><summary>Search text for onion addresses.</summary>
<div class="doc-comment">
<p>Search text for onion addresses.</p>
<p></p>
<p>Returns:</p>
<p>List of (address, type) tuples</p>
</div>
</details>
</li>
<li><code>parse_certificate</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Parse X.509 certificate from PEM format.</span></li>
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
<li><code>to_dms</code> (document_intelligence.py) — <span class="doc-comment-inline">Convert decimal degrees to DMS (Degrees, Minutes, Seconds).</span></li>
<li><code>_detect_suspicious_content</code> (document_intelligence.py)
<details><summary>Detect suspicious keywords in text using Aho-Corasick if available.</summary>
<div class="doc-comment">
<p>Detect suspicious keywords in text using Aho-Corasick if available.</p>
<p></p>
<p>Lazy integration (Sprint 8AW): ahocorasick is NOT loaded on boot.</p>
<p>On first call, the automaton is built once and reused.</p>
<p>Falls back to substring scan if aho_extractor is unavailable.</p>
</div>
</details>
</li>
<li><code>_basic_image_analysis</code> (document_intelligence.py) — <span class="doc-comment-inline">Basic analysis without PIL.</span></li>
<li><code>_ensure_processes</code> (document_intelligence.py) — <span class="doc-comment-inline">Ensure worker processes are running (pool instead of single server).</span></li>
<li><code>close</code> (document_intelligence.py) — <span class="doc-comment-inline">Clean up resources: forensics thread pool and stegdetect server.</span></li>
<li><code>_detect_periodicity</code> (pattern_mining.py) — <span class="doc-comment-inline">Detect periodic patterns using FFT.</span></li>
<li><code>__init__</code> (identity_stitching.py)</li>
<li><code>_update_profile</code> (identity_stitching.py) — <span class="doc-comment-inline">Update an existing profile (frozen dataclass — uses object.__setattr__).</span></li>
<li><code>update</code> (academic_search.py) — <span class="doc-comment-inline">Update performance metrics.</span></li>
<li><code>_get_rust_batch_classify</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Lazy load Rust batch_ip_classify, fail-soft if unavailable.</span></li>
<li><code>_extract_tech_spacy</code> (web_intelligence.py) — <span class="doc-comment-inline">Extract tech keywords using spaCy PhraseMatcher.</span></li>
<li><code>initialize</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Initialize the crawler + Rust URL set.</span></li>
<li><code>_get_recommendations</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Get security recommendations.</span></li>
<li><code>analyze</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze Office document (sync).</span></li>
<li><code>__init__</code> (document_intelligence.py)
<details><summary>Initialize DeepForensicsAnalyzer.</summary>
<div class="doc-comment">
<p>Initialize DeepForensicsAnalyzer.</p>
<p></p>
<p>Args:</p>
<p>orch: Optional orchestrator reference for graph integration (S49-C)</p>
</div>
</details>
</li>
<li><code>_extract_temporal_preferences</code> (pattern_mining.py) — <span class="doc-comment-inline">Extract temporal preferences (preferred hours of activity).</span></li>
<li><code>_correlation_numpy</code> (pattern_mining.py) — <span class="doc-comment-inline">Calculate correlation using NumPy.</span></li>
<li><code>stats</code> (identity_stitching.py) — <span class="doc-comment-inline">Return cache statistics compatible with _BoundedCache API.</span></li>
<li><code>_simple_similarity</code> (identity_stitching.py) — <span class="doc-comment-inline">Simple similarity metric when rapidfuzz is not available.</span></li>
<li><code>__init__</code> (workflow_orchestrator.py)
<details><summary>Initialize workflow orchestrator.</summary>
<div class="doc-comment">
<p>Initialize workflow orchestrator.</p>
<p></p>
<p>Args:</p>
<p>orchestrator: Main orchestrator instance for module access</p>
<p>config: Optional intelligence configuration</p>
</div>
</details>
</li>
<li><code>scan_database_ports</code> (exposed_service_hunter.py)
<details><summary>Scan hosts for exposed database ports.</summary>
<div class="doc-comment">
<p>Scan hosts for exposed database ports.</p>
<p></p>
<p>Args:</p>
<p>hosts: List of hostnames or IPs</p>
<p></p>
<p>Returns:</p>
<p>List of exposed database services</p>
</div>
</details>
</li>
<li><code>set</code> (exposed_service_hunter.py)
<details><summary>Set cached value with current timestamp.</summary>
<div class="doc-comment">
<p>Set cached value with current timestamp.</p>
<p></p>
<p>Args:</p>
<p>key: Cache key</p>
<p>value: Value to cache</p>
</div>
</details>
</li>
<li><code>_risk_score_to_level</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Convert risk score to level string.</span></li>
<li><code>parse</code> (open_source_collectors.py)</li>
<li><code>parse</code> (open_source_collectors.py)</li>
<li><code>_parse_date</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Parse WHOIS date string.</span></li>
<li><code>_is_ip_address</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Check if target is IP address.</span></li>
<li><code>_analyze_personal_threats</code> (web_intelligence.py) — <span class="doc-comment-inline">Analyze OSINT data for personal threats.</span></li>
<li><code>_extract_tech_regex</code> (web_intelligence.py) — <span class="doc-comment-inline">Extract tech keywords using word-boundary regex (spaCy fallback).</span></li>
<li><code>_find_shared_attributes</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Find shared attributes between two timelines.</span></li>
<li><code>_get_bitcoin_address_type</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Get Bitcoin address type.</span></li>
<li><code>caesar_decrypt</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Decrypt Caesar cipher with given shift.</span></li>
<li><code>atbash_decrypt</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Decrypt Atbash cipher (reverse alphabet).</span></li>
<li><code>rail_fence_bruteforce</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Try all rail counts from 2 to max_rails.</span></li>
<li><code>_find_vigenere_key_length</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Find Vigenere key length using Index of Coincidence.</span></li>
<li><code>_find_caesar_shift</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Find most likely Caesar shift for text using frequency analysis.</span></li>
<li><code>_calculate_entropy</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Calculate Shannon entropy.</span></li>
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
<li><code>__post_init__</code> (relationship_discovery.py)</li>
<li><code>build_index</code> (relationship_discovery.py) — <span class="doc-comment-inline">Build LSH index from graph.</span></li>
<li><code>enable</code> (relationship_discovery.py) — <span class="doc-comment-inline">Initialize GNN predictor.</span></li>
<li><code>_add_predicted_edge</code> (relationship_discovery.py) — <span class="doc-comment-inline">Add predicted edge to graph.</span></li>
<li><code>clear</code> (relationship_discovery.py) — <span class="doc-comment-inline">Clear all data from the engine.</span></li>
<li><code>analyze_async</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze Office document with FOCA enrichment (async, M1-safe).</span></li>
<li><code>_ela_analysis</code> (document_intelligence.py)
<details><summary>Error Level Analysis - returns manipulation probability 0-1.</summary>
<div class="doc-comment">
<p>Error Level Analysis - returns manipulation probability 0-1.</p>
<p></p>
<p>Uses ProcessPool for CPU-bound analysis to avoid contention with MLX workers.</p>
<p>M1 8GB safe: max 2 workers in shared pool.</p>
</div>
</details>
</li>
<li><code>batch_analyze</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze multiple documents (sync wrapper for backward compatibility).</span></li>
<li><code>_cosine_similarity</code> (document_intelligence.py) — <span class="doc-comment-inline">Compute cosine similarity between two vectors.</span></li>
<li><code>_extract_keywords</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract high-value keywords from text.</span></li>
<li><code>clear</code> (identity_stitching.py) — <span class="doc-comment-inline">Clear all data from the engine.</span></li>
<li><code>optimize_memory</code> (identity_stitching.py) — <span class="doc-comment-inline">Optimize memory usage by clearing caches and forcing GC.</span></li>
<li><code>get_timeline</code> (archive_discovery.py) — <span class="doc-comment-inline">Get timeline of changes for a URL.</span></li>
<li><code>_extract_from_snapshots</code> (archive_discovery.py) — <span class="doc-comment-inline">Extract content from snapshots concurrently</span></li>
<li><code>_parse_transaction</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Parse raw transaction data into Transaction object.</span></li>
<li><code>_init_sources</code> (academic_search.py) — <span class="doc-comment-inline">Initialize source adapters.</span></li>
<li><code>_check_admission</code> (open_source_collectors.py) — <span class="doc-comment-inline">Check M1ResourceGovernor admission. Returns True if allowed.</span></li>
<li><code>probe_known_hashes</code> (network_reconnaissance.py)
<details><summary>Dotazovat DHT pro known malware info_hashes z MalwareBazaar.</summary>
<div class="doc-comment">
<p>Dotazovat DHT pro known malware info_hashes z MalwareBazaar.</p>
<p>Vrátí [(info_hash, status)].</p>
</div>
</details>
</li>
<li><code>_calculate_threat_score</code> (web_intelligence.py) — <span class="doc-comment-inline">Calculate overall threat score.</span></li>
<li><code>_score_to_threat_level</code> (web_intelligence.py) — <span class="doc-comment-inline">Convert threat score to threat level.</span></li>
<li><code>get_operation_results</code> (web_intelligence.py) — <span class="doc-comment-inline">Get comprehensive operation results.</span></li>
<li><code>_detect_disappearances</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect content disappearances.</span></li>
<li><code>_calculate_correlation_score</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Calculate correlation score between two timelines.</span></li>
<li><code>_find_temporal_proximity</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Find events that are temporally close.</span></li>
<li><code>analyze_bitcoin_address</code> (dark_web_intelligence.py)
<details><summary>Analyze Bitcoin address.</summary>
<div class="doc-comment">
<p>Analyze Bitcoin address.</p>
<p></p>
<p>Note: Without external APIs, we can only do basic validation.</p>
<p>For full analysis, would need blockchain.info or similar API.</p>
</div>
</details>
</li>
<li><code>cluster_addresses</code> (dark_web_intelligence.py)
<details><summary>Cluster addresses that might belong to the same entity.</summary>
<div class="doc-comment">
<p>Cluster addresses that might belong to the same entity.</p>
<p></p>
<p>Uses heuristics like:</p>
<p>- Common input ownership</p>
<p>- Change address patterns</p>
</div>
</details>
</li>
<li><code>_nvd_to_cve</code> (exposure_clients.py) — <span class="doc-comment-inline">Convert NVD vulnerability format to our CVE dict.</span></li>
<li><code>_index_of_coincidence</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Calculate Index of Coincidence (0.067 for English, 0.0385 for random).</span></li>
<li><code>parse_certificate_der</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Parse certificate from DER format.</span></li>
<li><code>correlate</code> (exposure_correlator.py)
<details><summary>Correlate exposure signals from findings.</summary>
<div class="doc-comment">
<p>Correlate exposure signals from findings.</p>
<p></p>
<p>Returns:</p>
<p>List of CanonicalFinding (source_type="exposure_correlation").</p>
</div>
</details>
</li>
<li><code>_find_relationship</code> (relationship_discovery.py) — <span class="doc-comment-inline">Find relationship between two entities.</span></li>
<li><code>optimize_memory</code> (relationship_discovery.py) — <span class="doc-comment-inline">Optimize memory usage by clearing caches and forcing GC.</span></li>
<li><code>close</code> (document_intelligence.py) — <span class="doc-comment-inline">Close FOCA extractor and release resources (fail-safe).</span></li>
<li><code>_extract_comments_from_xml</code> (document_intelligence.py) — <span class="doc-comment-inline">Extract comments from Word XML.</span></li>
<li><code>__init__</code> (document_intelligence.py)</li>
<li><code>_create_unknown_analysis</code> (document_intelligence.py) — <span class="doc-comment-inline">Create analysis for unknown file type.</span></li>
<li><code>_check_mlx</code> (document_intelligence.py) — <span class="doc-comment-inline">Check if MLX is available.</span></li>
<li><code>_parse_search_results</code> (archive_discovery.py) — <span class="doc-comment-inline">Parse Archive.today search results.</span></li>
<li><code>_fetch_bitcoin_transaction_detail</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Fetch detailed Bitcoin transaction.</span></li>
<li><code>_detect_rapid_trading</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Detect rapid trading pattern.</span></li>
<li><code>_normalize_url</code> (academic_search.py) — <span class="doc-comment-inline">Normalize URL for deduplication.</span></li>
<li><code>_get_governor</code> (open_source_collectors.py) — <span class="doc-comment-inline">Lazy load governor singleton to avoid circular imports and ensure consistent state.</span></li>
<li><code>reverse_lookup</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Perform reverse DNS lookup.</span></li>
<li><code>_extract_list</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Extract list field from WHOIS.</span></li>
<li><code>resolve_domain</code> (network_reconnaissance.py) — <span class="doc-comment-inline">A-record lookup — returns list of IPv4 addresses.</span></li>
<li><code>reverse_lookup</code> (network_reconnaissance.py) — <span class="doc-comment-inline">PTR record lookup — returns list of hostnames.</span></li>
<li><code>_init_metrics_and_config</code> (web_intelligence.py) — <span class="doc-comment-inline">Initialize metrics and configuration from config dict.</span></li>
<li><code>_extract_hiring_patterns</code> (web_intelligence.py) — <span class="doc-comment-inline">Detect hiring patterns in job posting text.</span></li>
<li><code>_extract_pain_points</code> (web_intelligence.py) — <span class="doc-comment-inline">Detect inferred pain points from job posting text.</span></li>
<li><code>_detect_identity_changes</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect identity changes in snapshots.</span></li>
<li><code>_index_of_coincidence</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Calculate Index of Coincidence.</span></li>
<li><code>_chi_square_test</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Perform chi-square test against uniform distribution.</span></li>
<li><code>_is_likely_encrypted</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Determine if data is likely encrypted.</span></li>
<li><code>paper_id</code> (academic_discovery.py) — <span class="doc-comment-inline">Paper ID usable with get_citations — prefer DOI, fallback to title-hash.</span></li>
<li><code>_invalidate_caches</code> (relationship_discovery.py) — <span class="doc-comment-inline">Invalidate all cached computations.</span></li>
<li><code>_parse_exif_datetime</code> (document_intelligence.py) — <span class="doc-comment-inline">Parse EXIF datetime string.</span></li>
<li><code>_estimate_optimal_chunk_size</code> (document_intelligence.py)
<details><summary>Estimate optimal chunk size based on available RAM.</summary>
<div class="doc-comment">
<p>Estimate optimal chunk size based on available RAM.</p>
<p></p>
<p>M1 8GB optimization: Target &lt; 5.5GB to leave room for system</p>
</div>
</details>
</li>
<li><code>_gini_coefficient</code> (pattern_mining.py) — <span class="doc-comment-inline">Calculate Gini coefficient for concentration.</span></li>
<li><code>_invalidate_caches</code> (identity_stitching.py) — <span class="doc-comment-inline">Invalidate all cached computations.</span></li>
<li><code>_extract_tweet_id</code> (archive_discovery.py) — <span class="doc-comment-inline">Extract tweet ID from Twitter/X URL</span></li>
<li><code>_add_timeline_event</code> (workflow_orchestrator.py)
<details><summary>Add event to execution timeline.</summary>
<div class="doc-comment">
<p>Add event to execution timeline.</p>
<p></p>
<p>Args:</p>
<p>event_type: Type of event</p>
<p>details: Event details</p>
</div>
</details>
</li>
<li><code>register_module</code> (workflow_orchestrator.py)
<details><summary>Register a module instance.</summary>
<div class="doc-comment">
<p>Register a module instance.</p>
<p></p>
<p>Args:</p>
<p>name: Module name</p>
<p>instance: Module instance</p>
</div>
</details>
</li>
<li><code>__aenter__</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Async context manager entry.</span></li>
<li><code>_fetch_ethereum_transactions</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Fetch Ethereum transactions from Etherscan.</span></li>
<li><code>resolve_aaaa</code> (network_reconnaissance.py) — <span class="doc-comment-inline">AAAA-record lookup — returns list of IPv6 addresses.</span></li>
<li><code>_analyze_web_vulnerabilities</code> (web_intelligence.py) — <span class="doc-comment-inline">Analyze web data for vulnerabilities.</span></li>
<li><code>_estimate_block_size</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Estimate block cipher block size using Kasiski-like method.</span></li>
<li><code>crack_classical_cipher</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Automatically crack classical cipher.</span></li>
<li><code>generate_password_hash</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Generate password hash.</span></li>
<li><code>_analyze_ole</code> (document_intelligence.py) — <span class="doc-comment-inline">Analyze legacy OLE format.</span></li>
<li><code>__init__</code> (document_intelligence.py)</li>
<li><code>update</code> (pattern_mining.py) — <span class="doc-comment-inline">Update statistics with new value.</span></li>
<li><code>__post_init__</code> (identity_stitching.py)</li>
<li><code>_register_profile_lsh</code> (identity_stitching.py)
<details><summary>Register profile fingerprint in LSH index. Call ONLY on first add.</summary>
<div class="doc-comment">
<p>Register profile fingerprint in LSH index. Call ONLY on first add.</p>
<p>LSH has no remove() — calling this on update would duplicate entries.</p>
</div>
</details>
</li>
<li><code>_is_error_page</code> (archive_discovery.py) — <span class="doc-comment-inline">Check if content is an error page</span></li>
<li><code>snapshots_one_shot</code> (archive_discovery.py)
<details><summary>One-shot CDX lookup — vytvoří a zavře vlastní session.</summary>
<div class="doc-comment">
<p>One-shot CDX lookup — vytvoří a zavře vlastní session.</p>
<p>USE CASE: compat layer, tests, ad-hoc volání bez externího session.</p>
<p>PRO: žádné unclosed session warnings.</p>
</div>
</details>
</li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>_get_client</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Get or create HTTP client.</span></li>
<li><code>_is_valid_address</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Validate address format for given chain.</span></li>
<li><code>_fetch_transactions</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Fetch raw transactions for an address.</span></li>
<li><code>_extract_field</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Extract single field from WHOIS.</span></li>
<li><code>_init_per_host_gate</code> (web_intelligence.py)
<details><summary>ISSUE #15 FIX: Per-host concurrency gate — prevents head-of-line blocking</summary>
<div class="doc-comment">
<p>ISSUE #15 FIX: Per-host concurrency gate — prevents head-of-line blocking</p>
<p>when multiple operations target the same host (e.g. example.com scraping).</p>
<p>BoundedPerHostGate uses LRU eviction at 512 hosts × 4 concurrent = ~128 KB RAM.</p>
</div>
</details>
</li>
<li><code>_track_task</code> (web_intelligence.py) — <span class="doc-comment-inline">Register an owned operation task. Silently drops if at capacity.</span></li>
<li><code>_calculate_timeline_confidence</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Calculate confidence score for timeline.</span></li>
<li><code>correlate</code> (passive_fingerprint.py)
<details><summary>Correlate fingerprints from findings.</summary>
<div class="doc-comment">
<p>Correlate fingerprints from findings.</p>
<p></p>
<p>Returns list of CanonicalFinding with source_type="passive_fingerprint".</p>
</div>
</details>
</li>
<li><code>_bounded_insert_content_cache</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Insert into content_cache with FIFO LRU eviction at limit.</span></li>
<li><code>_bounded_insert_visited_url</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Insert into visited_urls with FIFO LRU eviction at limit.</span></li>
<li><code>_bounded_insert_discovered_service</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Insert into discovered_services with FIFO eviction at limit.</span></li>
<li><code>_validate_bitcoin_address</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Basic Bitcoin address validation.</span></li>
<li><code>close</code> (exposure_clients.py)</li>
<li><code>_osv_severity</code> (exposure_clients.py) — <span class="doc-comment-inline">Extract severity from OSV format.</span></li>
<li><code>_is_base64</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Check if text is valid base64.</span></li>
<li><code>_detect_language</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Detect language of text.</span></li>
<li><code>__post_init__</code> (relationship_discovery.py)</li>
<li><code>_node_to_minhash</code> (relationship_discovery.py) — <span class="doc-comment-inline">Create MinHash from node's neighbors.</span></li>
<li><code>get_memory_usage</code> (relationship_discovery.py) — <span class="doc-comment-inline">Estimate memory usage of key data structures.</span></li>
<li><code>__del__</code> (document_intelligence.py) — <span class="doc-comment-inline">Fallback shutdown on garbage collection.</span></li>
<li><code>add_username</code> (identity_stitching.py) — <span class="doc-comment-inline">Add a username entry for a platform.</span></li>
<li><code>get_username</code> (identity_stitching.py) — <span class="doc-comment-inline">Get username for a specific platform.</span></li>
<li><code>get_memory_usage</code> (identity_stitching.py) — <span class="doc-comment-inline">Estimate memory usage of key data structures.</span></li>
<li><code>datetime</code> (archive_discovery.py) — <span class="doc-comment-inline">Parse timestamp as datetime.</span></li>
<li><code>__aenter__</code> (archive_discovery.py)</li>
<li><code>score</code> (academic_search.py) — <span class="doc-comment-inline">Calculate overall source score.</span></li>
<li><code>__init__</code> (network_reconnaissance.py)</li>
<li><code>_extract_email</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Extract email field, handling privacy protection.</span></li>
<li><code>__init__</code> (network_reconnaissance.py)</li>
<li><code>_analyze_personal_vulnerabilities</code> (web_intelligence.py) — <span class="doc-comment-inline">Analyze OSINT data for personal vulnerabilities.</span></li>
<li><code>get_operation_status</code> (web_intelligence.py) — <span class="doc-comment-inline">Get status of a specific operation.</span></li>
<li><code>correlate</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Correlate tech-stack signals from findings.</span></li>
<li><code>__init__</code> (dark_web_intelligence.py)</li>
<li><code>_mark_url_visited</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Mark URL as visited (Rust MmapUrlSet or fallback OrderedDict).</span></li>
<li><code>_open_env</code> (exposure_clients.py) — <span class="doc-comment-inline">Otevře LMDB env lazy.</span></li>
<li><code>__init__</code> (cryptographic_intelligence.py)</li>
<li><code>parse_certificate</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Parse X.509 certificate.</span></li>
<li><code>get_top_pairs</code> (relationship_discovery.py) — <span class="doc-comment-inline">Get top N entity pairs by affinity score.</span></li>
<li><code>__init__</code> (relationship_discovery.py)</li>
<li><code>get_candidates</code> (relationship_discovery.py) — <span class="doc-comment-inline">Return candidate nodes for prediction (≤1% of total).</span></li>
<li><code>__init__</code> (relationship_discovery.py)</li>
<li><code>get_source_credibility</code> (relationship_discovery.py) — <span class="doc-comment-inline">Get credibility score for source from bandit.</span></li>
<li><code>_ela_analysis_mps</code> (document_intelligence.py) — <span class="doc-comment-inline">MPS-accelerated ELA analysis (runs sync MPS in ProcessPool to avoid GIL).</span></li>
<li><code>_ela_analysis_cpu</code> (document_intelligence.py) — <span class="doc-comment-inline">CPU-based ELA analysis (runs in ProcessPool to avoid blocking MLX workers).</span></li>
<li><code>__init__</code> (pattern_mining.py)</li>
<li><code>get_top_k</code> (pattern_mining.py) — <span class="doc-comment-inline">Get top k most frequent items using heapq for O(n log k) performance (Sprint 26).</span></li>
<li><code>find</code> (identity_stitching.py)</li>
<li><code>groups</code> (identity_stitching.py)</li>
<li><code>put</code> (identity_stitching.py) — <span class="doc-comment-inline">Put item. Evicts on TTL expiry or memory pressure.</span></li>
<li><code>__post_init__</code> (identity_stitching.py)</li>
<li><code>_normalize_username</code> (identity_stitching.py) — <span class="doc-comment-inline">Normalize username for comparison.</span></li>
<li><code>_extract_title</code> (archive_discovery.py) — <span class="doc-comment-inline">Extract title from HTML.</span></li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>cleanup</code> (archive_discovery.py) — <span class="doc-comment-inline">Cleanup resources</span></li>
<li><code>_throttle</code> (archive_discovery.py)</li>
<li><code>_throttle</code> (archive_discovery.py)</li>
<li><code>_throttle</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Async context manager exit.</span></li>
<li><code>__exit__</code> (exposed_service_hunter.py)</li>
<li><code>__del__</code> (exposed_service_hunter.py)</li>
<li><code>_generate_cluster_id</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Generate a unique cluster ID from addresses.</span></li>
<li><code>_is_likely_contract</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Heuristic: check if address is likely a contract.</span></li>
<li><code>close</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Close HTTP client and cleanup resources.</span></li>
<li><code>_extract_key_terms</code> (academic_search.py) — <span class="doc-comment-inline">Extract key terms from query.</span></li>
<li><code>cleanup</code> (academic_search.py) — <span class="doc-comment-inline">Cleanup resources.</span></li>
<li><code>_throttle</code> (academic_search.py)</li>
<li><code>search_pastebin</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search paste sites for secrets/leaks.</span></li>
<li><code>search_usenet</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search Usenet archives.</span></li>
<li><code>search_matrix</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search public Matrix rooms.</span></li>
<li><code>search_academic</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search academic preprint servers.</span></li>
<li><code>search_sec_edgar</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search SEC EDGAR filings.</span></li>
<li><code>search_court_records</code> (open_source_collectors.py) — <span class="doc-comment-inline">Search federal court cases.</span></li>
<li><code>_recon_ip</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Reconnaissance for IP address.</span></li>
<li><code>__init__</code> (network_reconnaissance.py)</li>
<li><code>_get_init_lock</code> (web_intelligence.py) — <span class="doc-comment-inline">ISSUE-014 FIX: Lazily create init lock in the current event loop.</span></li>
<li><code>_update_success_rate</code> (web_intelligence.py) — <span class="doc-comment-inline">Update operation success rate.</span></li>
<li><code>_content_similarity</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Calculate similarity between two content strings.</span></li>
<li><code>_get_bfs_lock</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">ISSUE-014 FIX: Lazily create BFS lock in the current event loop.</span></li>
<li><code>_is_url_visited</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Check if URL was visited (Rust MmapUrlSet or fallback OrderedDict).</span></li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>_throttle</code> (exposure_clients.py)</li>
<li><code>_throttle</code> (exposure_clients.py)</li>
<li><code>_throttle</code> (exposure_clients.py)</li>
<li><code>analyze_hash</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Analyze hash value.</span></li>
<li><code>__eq__</code> (relationship_discovery.py)</li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>__init__</code> (pattern_mining.py)</li>
<li><code>__init__</code> (identity_stitching.py)</li>
<li><code>get</code> (identity_stitching.py) — <span class="doc-comment-inline">Get item. Returns None on miss or expired.</span></li>
<li><code>_extract_email_domain</code> (identity_stitching.py) — <span class="doc-comment-inline">Extract domain from email address.</span></li>
<li><code>_extract_words</code> (identity_stitching.py) — <span class="doc-comment-inline">Extract words from text.</span></li>
<li><code>export_matches</code> (identity_stitching.py) — <span class="doc-comment-inline">Export all matches as list of dictionaries.</span></li>
<li><code>export_stitched</code> (identity_stitching.py) — <span class="doc-comment-inline">Export stitched identities as list of dictionaries.</span></li>
<li><code>__post_init__</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (archive_discovery.py)</li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (archive_discovery.py)</li>
<li><code>_select_best_content</code> (archive_discovery.py) — <span class="doc-comment-inline">Select best content from results</span></li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aexit__</code> (archive_discovery.py)</li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aenter__</code> (exposed_service_hunter.py)</li>
<li><code>__aexit__</code> (exposed_service_hunter.py)</li>
<li><code>__aenter__</code> (exposed_service_hunter.py)</li>
<li><code>__aexit__</code> (exposed_service_hunter.py)</li>
<li><code>__aenter__</code> (exposed_service_hunter.py)</li>
<li><code>__aexit__</code> (exposed_service_hunter.py)</li>
<li><code>__aenter__</code> (exposed_service_hunter.py)</li>
<li><code>__aexit__</code> (exposed_service_hunter.py)</li>
<li><code>clear</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Clear all cached entries.</span></li>
<li><code>_analyze_generic_wallet</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Generic wallet analysis when specific API unavailable.</span></li>
<li><code>__post_init__</code> (academic_search.py)</li>
<li><code>__post_init__</code> (academic_search.py)</li>
<li><code>success_rate</code> (academic_search.py)</li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>_parse_whois</code> (network_reconnaissance.py) — <span class="doc-comment-inline">Parse raw WHOIS data into structured format.</span></li>
<li><code>_analyze_security_indicators</code> (web_intelligence.py) — <span class="doc-comment-inline">Analyze web data for security indicators.</span></li>
<li><code>lifespan_days</code> (temporal_archaeologist.py)</li>
<li><code>__aenter__</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Async context manager entry.</span></li>
<li><code>_recover_from_common_crawl</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Recover content from Common Crawl index.</span></li>
<li><code>_detect_sudden_changes</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Detect sudden changes in metadata or content.</span></li>
<li><code>clear_cache</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Clear internal cache.</span></li>
<li><code>close</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Close Tor connections.</span></li>
<li><code>__aenter__</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Async context manager entry - initializes Tor connection.</span></li>
<li><code>close</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Close crawler and cleanup session state.</span></li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>_get_session</code> (exposure_clients.py)</li>
<li><code>_get_session</code> (exposure_clients.py)</li>
<li><code>_osv_affected</code> (exposure_clients.py) — <span class="doc-comment-inline">Extract affected packages from OSV format.</span></li>
<li><code>_get_hash_function</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Get Python hash function for type.</span></li>
<li><code>reset</code> (exposure_correlator.py) — <span class="doc-comment-inline">Reset internal state and stats.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert entity to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert relationship to dictionary.</span></li>
<li><code>__post_init__</code> (relationship_discovery.py)</li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert path to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert community to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert affinity matrix to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert communication to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert document to dictionary.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Convert influence model to dictionary.</span></li>
<li><code>add_document</code> (relationship_discovery.py) — <span class="doc-comment-inline">S49-E: Track URL to node mapping for quick lookup.</span></li>
<li><code>get_entity</code> (relationship_discovery.py) — <span class="doc-comment-inline">Get an entity by ID.</span></li>
<li><code>export_graph</code> (relationship_discovery.py) — <span class="doc-comment-inline">Export the relationship graph as NetworkX graph.</span></li>
<li><code>to_dict</code> (relationship_discovery.py) — <span class="doc-comment-inline">Export engine state as dictionary.</span></li>
<li><code>get_stats</code> (relationship_discovery.py) — <span class="doc-comment-inline">Get engine statistics.</span></li>
<li><code>to_google_maps_url</code> (document_intelligence.py) — <span class="doc-comment-inline">Generate Google Maps URL.</span></li>
<li><code>__init__</code> (document_intelligence.py)</li>
<li><code>_stegdetect</code> (document_intelligence.py) — <span class="doc-comment-inline">Run stegdetect on image using persistent server.</span></li>
<li><code>ensure_running</code> (document_intelligence.py) — <span class="doc-comment-inline">Alias for _ensure_processes (Sprint 45 compatibility).</span></li>
<li><code>_run_forensics_async</code> (document_intelligence.py) — <span class="doc-comment-inline">Run async forensics analysis in a separate thread with its own event loop.</span></li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>__post_init__</code> (pattern_mining.py)</li>
<li><code>get_frequency</code> (pattern_mining.py) — <span class="doc-comment-inline">Get frequency of item in current window.</span></li>
<li><code>_normalize_key</code> (identity_stitching.py) — <span class="doc-comment-inline">Normalize key so (A,B) and (B,A) map to same slot.</span></li>
<li><code>clear</code> (identity_stitching.py) — <span class="doc-comment-inline">Clear all entries.</span></li>
<li><code>to_dict</code> (identity_stitching.py) — <span class="doc-comment-inline">Convert to dictionary.</span></li>
<li><code>__post_init__</code> (identity_stitching.py)</li>
<li><code>get_all_usernames</code> (identity_stitching.py) — <span class="doc-comment-inline">Get all usernames across platforms.</span></li>
<li><code>get_platforms</code> (identity_stitching.py) — <span class="doc-comment-inline">Get set of platforms where this identity appears.</span></li>
<li><code>to_dict</code> (identity_stitching.py) — <span class="doc-comment-inline">Convert profile to dictionary.</span></li>
<li><code>to_dict</code> (identity_stitching.py) — <span class="doc-comment-inline">Convert match to dictionary.</span></li>
<li><code>to_dict</code> (identity_stitching.py) — <span class="doc-comment-inline">Convert stitched identity to dictionary.</span></li>
<li><code>get_profile</code> (identity_stitching.py) — <span class="doc-comment-inline">Get a profile by ID.</span></li>
<li><code>_normalize_email</code> (identity_stitching.py) — <span class="doc-comment-inline">Normalize email for comparison.</span></li>
<li><code>_normalize_text</code> (identity_stitching.py) — <span class="doc-comment-inline">Normalize text for comparison.</span></li>
<li><code>to_dict</code> (identity_stitching.py) — <span class="doc-comment-inline">Export engine state as dictionary.</span></li>
<li><code>get_stats</code> (identity_stitching.py) — <span class="doc-comment-inline">Get engine statistics.</span></li>
<li><code>wayback_url</code> (archive_discovery.py) — <span class="doc-comment-inline">Get Wayback Machine URL for this snapshot.</span></li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aenter__</code> (archive_discovery.py)</li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aenter__</code> (archive_discovery.py)</li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>__aenter__</code> (archive_discovery.py)</li>
<li><code>get_statistics</code> (archive_discovery.py) — <span class="doc-comment-inline">Get resurrector statistics</span></li>
<li><code>__aenter__</code> (archive_discovery.py)</li>
<li><code>__init__</code> (archive_discovery.py)</li>
<li><code>to_dict</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Convert to dictionary.</span></li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>get_statistics</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Get hunter statistics.</span></li>
<li><code>close</code> (exposed_service_hunter.py) — <span class="doc-comment-inline">Close database connection.</span></li>
<li><code>_is_likely_exchange</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Heuristic: check if address is likely an exchange.</span></li>
<li><code>__aenter__</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Async context manager entry.</span></li>
<li><code>__aexit__</code> (blockchain_analyzer.py) — <span class="doc-comment-inline">Async context manager exit.</span></li>
<li><code>search</code> (academic_search.py) — <span class="doc-comment-inline">Search the source with the given query.</span></li>
<li><code>get_performance</code> (academic_search.py) — <span class="doc-comment-inline">Get performance metrics for this source.</span></li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>_analyze_query</code> (academic_search.py) — <span class="doc-comment-inline">Analyze the query for optimization.</span></li>
<li><code>get_source_performance</code> (academic_search.py) — <span class="doc-comment-inline">Get performance metrics for all sources.</span></li>
<li><code>__init__</code> (academic_search.py)</li>
<li><code>cleanup</code> (academic_search.py) — <span class="doc-comment-inline">Cleanup resources (placeholder for future connection/state cleanup).</span></li>
<li><code>build_url</code> (open_source_collectors.py) — <span class="doc-comment-inline">Return one URL or an ordered list of fallback URLs to try.</span></li>
<li><code>parse</code> (open_source_collectors.py) — <span class="doc-comment-inline">Parse the response body. Return None on parse error or empty body.</span></li>
<li><code>close</code> (open_source_collectors.py) — <span class="doc-comment-inline">Graceful shutdown — no-op since sessions are shared singletons.</span></li>
<li><code>close</code> (network_reconnaissance.py) — <span class="doc-comment-inline">No-op — kept for API consistency.</span></li>
<li><code>is_degraded</code> (web_intelligence.py) — <span class="doc-comment-inline">True pokud modul běží v degraded mode (chybí volitelné komponenty).</span></li>
<li><code>degradation_reason</code> (web_intelligence.py) — <span class="doc-comment-inline">Důvod degraded módu, pokud existuje.</span></li>
<li><code>queue_health</code> (web_intelligence.py) — <span class="doc-comment-inline">Read-only seam: queue pressure and aging status at a glance.</span></li>
<li><code>active_posture</code> (web_intelligence.py) — <span class="doc-comment-inline">Read-only seam: active vs queued posture.</span></li>
<li><code>completed_operations</code> (web_intelligence.py) — <span class="doc-comment-inline">Backward-compatible accessor for completed_operations (read-only copy).</span></li>
<li><code>completed_count</code> (web_intelligence.py) — <span class="doc-comment-inline">Read-only count of completed operations (bounded).</span></li>
<li><code>task_posture</code> (web_intelligence.py) — <span class="doc-comment-inline">Read-only snapshot of task ownership state.</span></li>
<li><code>get_system_metrics</code> (web_intelligence.py) — <span class="doc-comment-inline">Get comprehensive system metrics.</span></li>
<li><code>__post_init__</code> (temporal_archaeologist.py)</li>
<li><code>age_days</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Calculate age in days from now.</span></li>
<li><code>__post_init__</code> (temporal_archaeologist.py)</li>
<li><code>__aexit__</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Async context manager exit — pool manages session lifecycle.</span></li>
<li><code>_search_by_entity</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Search for archived versions by entity identifier.</span></li>
<li><code>_search_wayback_by_query</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Search Wayback by query string.</span></li>
<li><code>_search_common_crawl</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Search Common Crawl index.</span></li>
<li><code>get_statistics</code> (temporal_archaeologist.py) — <span class="doc-comment-inline">Get archaeologist statistics.</span></li>
<li><code>get_stats</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Return fingerprinting stats snapshot.</span></li>
<li><code>reset_stats</code> (passive_fingerprint.py) — <span class="doc-comment-inline">Reset fingerprinting stats.</span></li>
<li><code>reset_stats</code> (passive_fingerprint.py)</li>
<li><code>_get_tor_browser_ua</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Get Tor Browser User-Agent.</span></li>
<li><code>get_session</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Get httpx.AsyncClient configured for Tor.</span></li>
<li><code>__aexit__</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Async context manager exit - closes Tor connection.</span></li>
<li><code>get_statistics</code> (dark_web_intelligence.py) — <span class="doc-comment-inline">Get crawling statistics with bounded truth.</span></li>
<li><code>close</code> (exposure_clients.py) — <span class="doc-comment-inline">No-op — kept for API consistency with other clients.</span></li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>close</code> (exposure_clients.py) — <span class="doc-comment-inline">No-op — kept for API consistency.</span></li>
<li><code>__init__</code> (exposure_clients.py)</li>
<li><code>crack_hash</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Attempt to crack hash with dictionary attack.</span></li>
<li><code>detect_encryption</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Detect if data is encrypted.</span></li>
<li><code>analyze_certificate_security</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Analyze certificate security.</span></li>
<li><code>get_statistics</code> (cryptographic_intelligence.py) — <span class="doc-comment-inline">Get cryptographic analysis statistics.</span></li>
<li><code>get_stats</code> (exposure_correlator.py) — <span class="doc-comment-inline">Return latest correlation stats.</span></li>
<li><code>__hash__</code> (relationship_discovery.py)</li>
<li><code>__init__</code> (document_intelligence.py)</li>
<li><code>get_mean</code> (pattern_mining.py)</li>
<li><code>get_variance</code> (pattern_mining.py)</li>
<li><code>get_std</code> (pattern_mining.py)</li>
<li><code>count</code> (identity_stitching.py)</li>
<li><code>__len__</code> (identity_stitching.py)</li>
<li><code>to_dict</code> (archive_discovery.py)</li>
<li><code>is_archived</code> (archive_discovery.py)</li>
<li><code>domain</code> (archive_discovery.py)</li>
<li><code>to_dict</code> (archive_discovery.py)</li>
<li><code>__init__</code> (exposed_service_hunter.py)</li>
<li><code>__enter__</code> (exposed_service_hunter.py)</li>
<li><code>to_dict</code> (academic_search.py)</li>
<li><code>to_dict</code> (academic_search.py)</li>
<li><code>__aenter__</code> (academic_search.py)</li>
<li><code>__aexit__</code> (academic_search.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>to_finding_dict</code> (open_source_collectors.py)</li>
<li><code>build_url</code> (open_source_collectors.py)</li>
<li><code>parse</code> (open_source_collectors.py)</li>
<li><code>build_url</code> (open_source_collectors.py)</li>
<li><code>build_url</code> (open_source_collectors.py)</li>
<li><code>__init__</code> (open_source_collectors.py)</li>
<li><code>to_dict</code> (temporal_archaeologist.py)</li>
<li><code>first_seen</code> (temporal_archaeologist.py)</li>
<li><code>last_seen</code> (temporal_archaeologist.py)</li>
<li><code>total_snapshots</code> (temporal_archaeologist.py)</li>
<li><code>to_dict</code> (temporal_archaeologist.py)</li>
<li><code>__init__</code> (passive_fingerprint.py)</li>
<li><code>__init__</code> (passive_fingerprint.py)</li>
<li><code>get_stats</code> (passive_fingerprint.py)</li>
<li><code>__init__</code> (dark_web_intelligence.py)</li>
<li><code>_make_key</code> (exposure_clients.py)</li>
<li><code>close</code> (exposure_clients.py)</li>
<li><code>close</code> (exposure_clients.py)</li>
<li><code>_get_session</code> (exposure_clients.py)</li>
<li><code>close</code> (exposure_clients.py)</li>
<li><code>__init__</code> (cryptographic_intelligence.py)</li>
<li><code>has_bucket</code> (exposure_correlator.py)</li>
<li><code>has_cert</code> (exposure_correlator.py)</li>
<li><code>has_jarm</code> (exposure_correlator.py)</li>
<li><code>has_dns</code> (exposure_correlator.py)</li>
<li><code>__init__</code> (exposure_correlator.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (107)</summary>
<ul>
<li><code>NETWORKX_AVAILABLE</code> (relationship_discovery.py)</li>
<li><code>_SESSION_ENGINE</code> (relationship_discovery.py)</li>
<li><code>SCIPY_AVAILABLE</code> (relationship_discovery.py)</li>
<li><code>DOCUMENT_INTELLIGENCE_AVAILABLE</code> (document_intelligence.py)</li>
<li><code>MPS_AVAILABLE</code> (document_intelligence.py)</li>
<li><code>_AHO_AVAILABLE</code> (document_intelligence.py)</li>
<li><code>MAX_IMAGE_SIZE</code> (document_intelligence.py)</li>
<li><code>_MAMBA_AVAILABLE</code> (pattern_mining.py)</li>
<li><code>_MAMBA_MODEL</code> (pattern_mining.py)</li>
<li><code>_MAMBA_TOKENIZER</code> (pattern_mining.py)</li>
<li><code>_MAMBA_FAILURES</code> (pattern_mining.py)</li>
<li><code>_MAMBA_DISABLED_UNTIL</code> (pattern_mining.py)</li>
<li><code>T</code> (identity_stitching.py)</li>
<li><code>NETWORKX_AVAILABLE</code> (identity_stitching.py)</li>
<li><code>IGRAPH_AVAILABLE</code> (identity_stitching.py)</li>
<li><code>SKLEARN_AVAILABLE</code> (identity_stitching.py)</li>
<li><code>SELECTOLAX_AVAILABLE</code> (archive_discovery.py)</li>
<li><code>MAX_PAYLOAD_BYTES</code> (archive_discovery.py)</li>
<li><code>MODULE_TIMEOUT</code> (workflow_orchestrator.py)</li>
<li><code>HIGH_RISK_PATTERNS</code> (workflow_orchestrator.py)</li>
<li><code>SEVERITY_WEIGHTS</code> (workflow_orchestrator.py)</li>
<li><code>MAX_CACHE_SIZE</code> (blockchain_analyzer.py)</li>
<li><code>KNOWN_SERVICES</code> (blockchain_analyzer.py)</li>
<li><code>BITCOIN_PATTERNS</code> (blockchain_analyzer.py)</li>
<li><code>ETHEREUM_PATTERN</code> (blockchain_analyzer.py)</li>
<li><code>MAX_PASTE_RESULTS</code> (open_source_collectors.py)</li>
<li><code>MAX_USENET_ARTICLES</code> (open_source_collectors.py)</li>
<li><code>MAX_CHAT_MESSAGES</code> (open_source_collectors.py)</li>
<li><code>MAX_ACADEMIC_PAPERS</code> (open_source_collectors.py)</li>
<li><code>MAX_SEC_FILINGS</code> (open_source_collectors.py)</li>
<li><code>MAX_COURT_CASES</code> (open_source_collectors.py)</li>
<li><code>RATE_LIMIT_S</code> (open_source_collectors.py)</li>
<li><code>TIMEOUT_S</code> (open_source_collectors.py)</li>
<li><code>_SECRET_REDACT_LEN</code> (open_source_collectors.py)</li>
<li><code>_RE_EMAIL</code> (open_source_collectors.py)</li>
<li><code>_RE_IPV4</code> (open_source_collectors.py)</li>
<li><code>_RE_IPV6</code> (open_source_collectors.py)</li>
<li><code>_RE_AWS_KEY</code> (open_source_collectors.py)</li>
<li><code>_RE_BEARER</code> (open_source_collectors.py)</li>
<li><code>_RE_PKEY</code> (open_source_collectors.py)</li>
<li><code>_RE_TOKEN</code> (open_source_collectors.py)</li>
<li><code>_PASTE_RATE_LIMIT_S</code> (open_source_collectors.py)</li>
<li><code>PRIVATEBIN_ADAPTER</code> (open_source_collectors.py)</li>
<li><code>GHOSTBIN_ADAPTER</code> (open_source_collectors.py)</li>
<li><code>ZEROBIN_ADAPTER</code> (open_source_collectors.py)</li>
<li><code>_PASTE_CACHE_MAX</code> (open_source_collectors.py)</li>
<li><code>_PASTE_CACHE_TTL_S</code> (open_source_collectors.py)</li>
<li><code>_PASTE_CACHE_EVICT_FRAC</code> (open_source_collectors.py)</li>
<li><code>_PASTE_HOST_SEMAPHORE</code> (open_source_collectors.py)</li>
<li><code>_USENET_RATE_LIMIT_S</code> (open_source_collectors.py)</li>
<li><code>_MATRIX_RATE_LIMIT_S</code> (open_source_collectors.py)</li>
<li><code>_ACADEMIC_RATE_LIMIT_S</code> (open_source_collectors.py)</li>
<li><code>_SEC_RATE_LIMIT_S</code> (open_source_collectors.py)</li>
<li><code>_COURT_RATE_LIMIT_S</code> (open_source_collectors.py)</li>
<li><code>_IMPORT_ERROR</code> (web_intelligence.py)</li>
<li><code>MAX_FINGERPRINT_FINDINGS</code> (passive_fingerprint.py)</li>
<li><code>MAX_FINGERPRINTS_PER_FINDING</code> (passive_fingerprint.py)</li>
<li><code>MAX_PATTERN_BYTES</code> (passive_fingerprint.py)</li>
<li><code>FINGERPRINT_TIMEOUT_S</code> (passive_fingerprint.py)</li>
<li><code>_HTTP_SERVER_PATTERNS</code> (passive_fingerprint.py)</li>
<li><code>_HTTP_HEADER_PATTERNS</code> (passive_fingerprint.py)</li>
<li><code>_TLS_CERT_PATTERNS</code> (passive_fingerprint.py)</li>
<li><code>_CT_CERT_PATTERNS</code> (passive_fingerprint.py)</li>
<li><code>_HTML_PATTERNS</code> (passive_fingerprint.py)</li>
<li><code>_PROTOCOL_PATTERNS</code> (passive_fingerprint.py)</li>
<li><code>_GLOBAL_STATS</code> (passive_fingerprint.py)</li>
<li><code>_MAX_TECH_STACK_FINDINGS</code> (passive_fingerprint.py)</li>
<li><code>_MAX_TECH_STACK_PER_FINDING</code> (passive_fingerprint.py)</li>
<li><code>_MAX_EVIDENCE_SAMPLE</code> (passive_fingerprint.py)</li>
<li><code>_TECH_STACK_PATTERNS</code> (passive_fingerprint.py)</li>
<li><code>_CMS_VERSION_PATTERNS</code> (passive_fingerprint.py)</li>
<li><code>TOR_AVAILABLE</code> (dark_web_intelligence.py)</li>
<li><code>_RUST_URL_SET_AVAILABLE</code> (dark_web_intelligence.py)</li>
<li><code>EXPOSURE_CACHE_ROOT</code> (exposure_clients.py)</li>
<li><code>_EXPOSURE_CACHE_TTL</code> (exposure_clients.py)</li>
<li><code>_CVE_CACHE_TTL</code> (exposure_clients.py)</li>
<li><code>_DB_EXECUTOR</code> (exposure_clients.py)</li>
<li><code>MAX_ASSETS</code> (exposure_correlator.py)</li>
<li><code>MAX_SIGNALS_PER_ASSET</code> (exposure_correlator.py)</li>
<li><code>MAX_FINDINGS</code> (exposure_correlator.py)</li>
<li><code>MAX_BUCKET_CANDIDATES_PER_ENTITY</code> (exposure_correlator.py)</li>
<li><code>MAX_BUCKET_CHECKS_PARALLEL</code> (exposure_correlator.py)</li>
<li><code>MAX_SUBDOMAIN_TAKEOVER_SUBDOMAINS</code> (exposure_correlator.py)</li>
<li><code>MAX_CLOUD_FINDINGS</code> (exposure_correlator.py)</li>
<li><code>SIGNAL_TYPE_CT_CERT</code> (exposure_correlator.py)</li>
<li><code>SIGNAL_TYPE_OPEN_BUCKET</code> (exposure_correlator.py)</li>
<li><code>SIGNAL_TYPE_JARM</code> (exposure_correlator.py)</li>
<li><code>SIGNAL_TYPE_PASSIVE_DNS</code> (exposure_correlator.py)</li>
<li><code>SIGNAL_TYPE_PASSIVE_FINGERPRINT</code> (exposure_correlator.py)</li>
<li><code>CORR_EXPOSED_HOST</code> (exposure_correlator.py)</li>
<li><code>CORR_CERT_DOMAIN</code> (exposure_correlator.py)</li>
<li><code>CORR_OPEN_BUCKET</code> (exposure_correlator.py)</li>
<li><code>CORR_SUSPICIOUS_FP</code> (exposure_correlator.py)</li>
<li><code>CORR_INFRA_CLUSTER</code> (exposure_correlator.py)</li>
<li><code>CORR_SUBDOMAIN_TAKEOVER</code> (exposure_correlator.py)</li>
<li><code>_SUSPICIOUS_JARM_PREFIXES</code> (exposure_correlator.py)</li>
<li><code>_S3_SUFFIXES</code> (exposure_correlator.py)</li>
<li><code>_CLOUD_BUCKET_TEMPLATES</code> (exposure_correlator.py)</li>
<li><code>_SUBDOMAIN_TAKEOVER_PROVIDERS</code> (exposure_correlator.py)</li>
<li><code>_GENERIC_HOSTING_JARM_PREFIXES</code> (exposure_correlator.py)</li>
<li><code>OPENALEX_BASE</code> (academic_discovery.py)</li>
<li><code>IARCHIVE_SCHOLAR</code> (academic_discovery.py)</li>
<li><code>CORE_API</code> (academic_discovery.py)</li>
<li><code>BIORXIV_API</code> (academic_discovery.py)</li>
<li><code>MEDRXIV_API</code> (academic_discovery.py)</li>
<li><code>MAX_CITATION_PAPERS</code> (academic_discovery.py)</li>
<li><code>MAX_HOPS</code> (academic_discovery.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 81 |
| Total lines | 39621 |
| Avg lines/file | 489 |
| Languages | Python |
| Outgoing deps | 3 |
| Incoming deps | 0 |
| Tier | 1 |

