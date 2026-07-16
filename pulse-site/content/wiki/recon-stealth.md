+++
title = "recon/stealth/"
weight = 13
description = "<think> Let me analyze this module's structure and purpose based on the provided context:  **Module: recon/stealth**  This module handles stealth web scraping and monitoring. It provides: 1. Stealth w..."

[extra]
tier = 2
file_count = 4
total_lines = 1749
languages = "Python"
parent_path = "recon"
+++

<think>
Let me analyze this module's structure and purpose based on the provided context:

**Module: recon/stealth**

This module handles stealth web scraping and monitoring. It provides:
1. Stealth web crawlers with TLS fingerprinting
2. Protection detection and bypass capabilities
3. Web monitoring with change detection and alerting

Key characteristics:
- 4 Python files, ~1750 total lines
- No outgoing dependencies detected
- No incoming dependencies detected (Tier 2)
- High fan-out for a key symbol `search` which is referenced in 262 files

Wait, there's something interesting here. The dependencies section says "No incoming dependencies detected" and "No outgoing dependencies detected" but the key symbols show `search` being referenced in 262 files. This seems contradictory, but perhaps the dependency graph doesn't capture all references, or perhaps this module is a leaf/isolated module that doesn't formally import from other modules but provides functionality used by many.

Let me write the overview focusing on:
1. Purpose: What this module does
2. Architectural role: It's isolated (no deps detected) but highly referenced
3. Scale/complexity: Medium-sized module with 4 files, ~1750 lines
4. Key abstractions: Stealth Crawler, Stealth WebScraper, Streaming Monitor

I need to NOT start with "The X module consists of..." or similar patterns.

Let me draft:

This module provides stealth web scraping and continuous source monitoring capabilities, enabling content extraction while evading anti-bot protections like Cloudflare, Akamai, and Data Dome. It achieves this through TLS fingerprint impersonation, dynamic header rotation, and multi-layer bypass strategies including proxy rotation and cloudscraper integration. The monitoring subsystem continuously tracks web sources (RSS feeds, APIs, URLs) for changes using content hashing, entity extraction, and keyword matching to generate severity-ranked alerts. With roughly 1750 lines across four files, this isolated module has no external dependencies but exposes the `search` function to 262 callers, making it a high-leverage utility despite its self-contained design. Changes here could affect a significant portion of the codebase given its extensive consumption.

</think>

This module provides stealth web scraping and continuous source monitoring capabilities, enabling content extraction while evading anti-bot protections such as Cloudflare, Akamai, and Data Dome. It achieves evasion through TLS fingerprint impersonation, dynamic header rotation, and multi-layer bypass strategies including proxy rotation and cloudscraper integration. The monitoring subsystem continuously tracks web sources—RSS feeds, APIs, and scheduled URLs—for changes using content hashing, entity extraction, and keyword matching to generate severity-ranked alerts. Despite having no external dependencies and residing at Tier 2 in the architecture, this module's `search` function alone is referenced by 262 files, making it a critical utility with wide blast radius if modified. With approximately 1750 lines across four files, it represents a medium-complexity, self-contained library that other modules consume for their data acquisition needs.

## Structure

| Language | Files |
|---|---|
| Python | 4 |

### Largest Files

- `scraper.py` (648 lines)
- `monitor.py` (621 lines)
- `_models.py` (404 lines)
- `__init__.py` (76 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>search</code> (Function) in scraper.py — referenced in 262 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, _catalog.py, _discovery_stage.py, _fix_rename.py, _models.py +250 more</li></ul>
</li>
<li>
<p><code>StealthCrawler</code> (Class) in scraper.py — referenced in 12 files</p>
<details><summary>Stealth web crawler with TLS fingerprinting.</summary>
<div class="doc-comment">
<p>Stealth web crawler with TLS fingerprinting.</p>
<p></p>
<p>From deep_research/distributed_dark_web_crawler.py:</p>
<p>- curl_cffi for TLS fingerprinting (impersonate="chrome136")</p>
<p>- DuckDuckGo HTML scraping (no CAPTCHA)</p>
<p>- Google fallback</p>
<p>- Zero memory leaks (M1 optimized)</p>
<p></p>
<p>Enhanced with stealth_toolkit:</p>
<p>- HeaderSpoofer for dynamic header rotation</p>
<p>- User-Agent rotation</p>
<p>- Platform-specific headers</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, enhanced_research.py, fetch_coordinator.py, lane_dispatch.py, monitor.py +4 more</li></ul>
</li>
<li>
<p><code>scrape</code> (Function) in scraper.py — referenced in 10 files</p>
<details><summary>Scrape a URL with protection detection and bypass.</summary>
<div class="doc-comment">
<p>Scrape a URL with protection detection and bypass.</p>
<p></p>
<p>Args:</p>
<p>url: URL to scrape</p>
<p>**kwargs: Additional options (headers, proxies, etc.)</p>
<p></p>
<p>Returns:</p>
<p>ScrapingResult with content or error</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: analytics_hook.py, archive_discovery.py, data_leak_hunter.py, dspy_compile.py, enhanced_research.py +4 more</li></ul>
</li>
<li>
<p><code>StealthWebScraper</code> (Class) in scraper.py — referenced in 7 files</p>
<details><summary>Advanced stealth web scraper with protection detection and bypass.</summary>
<div class="doc-comment">
<p>Advanced stealth web scraper with protection detection and bypass.</p>
<p></p>
<p>Enhanced with stealth_toolkit:</p>
<p>- Protection detection (Cloudflare, Akamai, Imperva, DataDome)</p>
<p>- Multi-layer bypass (cloudscraper, proxy rotation)</p>
<p>- Fingerprint rotation (50+ profiles)</p>
<p>- Async support via asyncio.to_thread for M1 compatibility</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, enhanced_research.py, fetch_coordinator.py, security_coordinator.py, stealth_crawler.py</li></ul>
</li>
<li>
<p><code>TorProxyManager</code> (Class) in _models.py — referenced in 7 files</p>
<details><summary>Check if Tor SOCKS proxy is running on port 9050.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, dark_web_intelligence.py, dark_web_lane.py, monitor.py, scraper.py +1 more</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (56)</summary>
<ul>
<li><code>_check_source</code> (monitor.py)
<details><summary>Check a single source for changes.</summary>
<div class="doc-comment">
<p>Check a single source for changes.</p>
<p></p>
<p>M1 8GB Optimized:</p>
<p>- Uses HEAD request first to check if content changed</p>
<p>- Connection reuse via session</p>
<p>- Minimal memory allocation</p>
</div>
</details>
</li>
<li><code>scrape</code> (scraper.py)
<details><summary>Scrape a URL with protection detection and bypass.</summary>
<div class="doc-comment">
<p>Scrape a URL with protection detection and bypass.</p>
<p></p>
<p>Args:</p>
<p>url: URL to scrape</p>
<p>**kwargs: Additional options (headers, proxies, etc.)</p>
<p></p>
<p>Returns:</p>
<p>ScrapingResult with content or error</p>
</div>
</details>
</li>
<li><code>_detect_changes</code> (monitor.py)</li>
<li><code>search</code> (scraper.py)</li>
<li><code>get_headers</code> (_models.py)</li>
<li><code>_head_check_changed</code> (monitor.py)
<details><summary>Use HEAD request to check if content changed.</summary>
<div class="doc-comment">
<p>Use HEAD request to check if content changed.</p>
<p></p>
<p>M1 8GB Optimization: Avoids downloading full content if not needed.</p>
</div>
</details>
</li>
<li><code>_extract_entities</code> (monitor.py) — <span class="doc-comment-inline">Extract entities from content using simple patterns.</span></li>
<li><code>_parse_duckduckgo</code> (scraper.py)</li>
<li><code>_detect_protection</code> (scraper.py)</li>
<li><code>_monitor_loop</code> (monitor.py) — <span class="doc-comment-inline">Main monitoring loop - M1 8GB optimized.</span></li>
<li><code>_fetch_html</code> (scraper.py)
<details><summary>Fetch HTML using available library with subprocess curl fallback (Sprint 8R).</summary>
<div class="doc-comment">
<p>Fetch HTML using available library with subprocess curl fallback (Sprint 8R).</p>
<p></p>
<p>Bounded fix TICKET-004: Never use subprocess on M1 (tickover only).</p>
</div>
</details>
</li>
<li><code>_parse_brave</code> (scraper.py) — <span class="doc-comment-inline">Parse Brave Search HTML results (Sprint 8R).</span></li>
<li><code>_parse_google</code> (scraper.py) — <span class="doc-comment-inline">Parse Google HTML results.</span></li>
<li><code>_process_event</code> (monitor.py) — <span class="doc-comment-inline">Process a stream event - generate alerts and store.</span></li>
<li><code>_determine_severity</code> (monitor.py)</li>
<li><code>_crawler_domain_allowed</code> (_models.py)
<details><summary>Check if a URL is allowed for crawling based on domain rules.</summary>
<div class="doc-comment">
<p>Check if a URL is allowed for crawling based on domain rules.</p>
<p></p>
<p>Args:</p>
<p>url: URL to check</p>
<p>surface: Calling surface name (for future domain allowlist/blocklist)</p>
<p></p>
<p>Returns:</p>
<p>(allowed, reason).</p>
</div>
</details>
</li>
<li><code>_load_fingerprint_profiles</code> (scraper.py) — <span class="doc-comment-inline">Load TLS/HTTP fingerprint profiles for rotation.</span></li>
<li><code>_fetch_content</code> (scraper.py)</li>
<li><code>add_source</code> (monitor.py)
<details><summary>Add a source to monitor.</summary>
<div class="doc-comment">
<p>Add a source to monitor.</p>
<p></p>
<p>Args:</p>
<p>source: MonitoredSource configuration</p>
<p></p>
<p>Returns:</p>
<p>True if source was added successfully</p>
</div>
</details>
</li>
<li><code>_fetch_rss</code> (monitor.py) — <span class="doc-comment-inline">Fetch and parse RSS/Atom feed using selectolax (async-native).</span></li>
<li><code>_search_duckduckgo</code> (scraper.py)</li>
<li><code>_search_brave</code> (scraper.py)</li>
<li><code>_search_google</code> (scraper.py)</li>
<li><code>is_running</code> (_models.py) — <span class="doc-comment-inline">Return True if Tor SOCKS port 9050 is reachable (cached, 5s TTL).</span></li>
<li><code>__init__</code> (monitor.py)</li>
<li><code>_fetch_url</code> (monitor.py) — <span class="doc-comment-inline">Fetch a general URL.</span></li>
<li><code>_fetch_with_curl_cffi_async</code> (scraper.py)</li>
<li><code>get_stealth_headers</code> (_models.py)
<details><summary>Return a default set of stealth HTTP headers for web scraping.</summary>
<div class="doc-comment">
<p>Return a default set of stealth HTTP headers for web scraping.</p>
<p></p>
<p>These headers mimic a real Chrome browser to reduce detection.</p>
</div>
</details>
</li>
<li><code>_check_dependencies</code> (scraper.py) — <span class="doc-comment-inline">Check for available HTTP libraries.</span></li>
<li><code>_fetch_with_curl_cffi</code> (scraper.py)</li>
<li><code>_check_dependencies</code> (scraper.py) — <span class="doc-comment-inline">Check for available libraries.</span></li>
<li><code>_fetch_api</code> (monitor.py) — <span class="doc-comment-inline">Fetch from API endpoint.</span></li>
<li><code>remove_source</code> (monitor.py)
<details><summary>Remove a source from monitoring.</summary>
<div class="doc-comment">
<p>Remove a source from monitoring.</p>
<p></p>
<p>Args:</p>
<p>source_id: ID of source to remove</p>
<p></p>
<p>Returns:</p>
<p>True if source was removed</p>
</div>
</details>
</li>
<li><code>_get_crawl_bloom</code> (_models.py) — <span class="doc-comment-inline">Lazy-open rotating mmap Bloom filter for URL dedup.</span></li>
<li><code>__init__</code> (scraper.py)</li>
<li><code>_fetch_with_cloudscraper</code> (scraper.py)</li>
<li><code>_fetch_with_httpx_fallback</code> (scraper.py)</li>
<li><code>_check_source_with_semaphore</code> (monitor.py)</li>
<li><code>start_monitoring</code> (monitor.py) — <span class="doc-comment-inline">Start the monitoring loop.</span></li>
<li><code>_cleanup_memory</code> (monitor.py) — <span class="doc-comment-inline">Periodic memory cleanup for M1 8GB optimization.</span></li>
<li><code>_fetch_with_httpx</code> (scraper.py) — <span class="doc-comment-inline">Fetch using httpx.Client (fallback).</span></li>
<li><code>stop_monitoring</code> (monitor.py) — <span class="doc-comment-inline">Stop the monitoring loop.</span></li>
<li><code>initialize</code> (monitor.py) — <span class="doc-comment-inline">Initialize the monitor with HTTP session.</span></li>
<li><code>_get_default_headers</code> (monitor.py) — <span class="doc-comment-inline">Get default HTTP headers for monitoring.</span></li>
<li><code>_check_dependencies</code> (monitor.py) — <span class="doc-comment-inline">Check for optional dependencies.</span></li>
<li><code>__init__</code> (scraper.py)</li>
<li><code>quick_scrape</code> (scraper.py)
<details><summary>Quick scrape a URL using default settings.</summary>
<div class="doc-comment">
<p>Quick scrape a URL using default settings.</p>
<p></p>
<p>Convenience function for one-off scraping without explicit setup.</p>
</div>
</details>
</li>
<li><code>__init__</code> (_models.py)</li>
<li><code>_match_keywords</code> (monitor.py)</li>
<li><code>__post_init__</code> (_models.py) — <span class="doc-comment-inline">Validate source configuration</span></li>
<li><code>_calculate_hash</code> (monitor.py) — <span class="doc-comment-inline">Calculate SHA256 hash of content.</span></li>
<li><code>_mark_surface_patched</code> (_models.py) — <span class="doc-comment-inline">Mark a surface as breaker-patched (called by each wired surface).</span></li>
<li><code>create_stealth_crawler</code> (scraper.py) — <span class="doc-comment-inline">Create a new StealthCrawler instance.</span></li>
<li><code>get_stealth_web_scraper</code> (scraper.py) — <span class="doc-comment-inline">Create a new StealthWebScraper instance.</span></li>
<li><code>_generate_id</code> (monitor.py) — <span class="doc-comment-inline">Generate a unique ID.</span></li>
<li><code>_mark_surface_unpatched</code> (_models.py) — <span class="doc-comment-inline">Mark a surface as unpatched (failed or too risky to modify).</span></li>
</ul>
</details>

<details><summary><strong>Class</strong> (20)</summary>
<ul>
<li><code>StreamingMonitor</code> (monitor.py)
<details><summary>Continuous monitoring system for web sources.</summary>
<div class="doc-comment">
<p>Continuous monitoring system for web sources.</p>
<p></p>
<p>Features:</p>
<p>- RSS feed monitoring with selectolax (async-native)</p>
<p>- API polling (Twitter/X, Reddit, custom APIs)</p>
<p>- Scheduled URL crawling with change detection</p>
<p>- Content hash comparison for efficient change detection</p>
<p>- Entity extraction from changes</p>
<p>- Keyword matching with alert generation</p>
<p>- M1 8GB optimized: async loops, connection reuse, selective fetching</p>
<p></p>
<p>Example:</p>
<p>&gt;&gt;&gt; crawler = StealthCrawler()</p>
<p>&gt;&gt;&gt; monitor = StreamingMonitor(crawler)</p>
<p>&gt;&gt;&gt; source = MonitoredSource(</p>
<p>...     source_id="news_rss",</p>
<p>...     source_type="rss",</p>
<p>...     url="https://example.com/feed.xml",</p>
<p>...     check_interval_minutes=30,</p>
<p>...     keywords=["security", "breach"]</p>
<p>... )</p>
<p>&gt;&gt;&gt; await monitor.add_source(source)</p>
<p>&gt;&gt;&gt; await monitor.start_monitoring()</p>
</div>
</details>
</li>
<li><code>StealthCrawler</code> (scraper.py)
<details><summary>Stealth web crawler with TLS fingerprinting.</summary>
<div class="doc-comment">
<p>Stealth web crawler with TLS fingerprinting.</p>
<p></p>
<p>From deep_research/distributed_dark_web_crawler.py:</p>
<p>- curl_cffi for TLS fingerprinting (impersonate="chrome136")</p>
<p>- DuckDuckGo HTML scraping (no CAPTCHA)</p>
<p>- Google fallback</p>
<p>- Zero memory leaks (M1 optimized)</p>
<p></p>
<p>Enhanced with stealth_toolkit:</p>
<p>- HeaderSpoofer for dynamic header rotation</p>
<p>- User-Agent rotation</p>
<p>- Platform-specific headers</p>
</div>
</details>
</li>
<li><code>StealthWebScraper</code> (scraper.py)
<details><summary>Advanced stealth web scraper with protection detection and bypass.</summary>
<div class="doc-comment">
<p>Advanced stealth web scraper with protection detection and bypass.</p>
<p></p>
<p>Enhanced with stealth_toolkit:</p>
<p>- Protection detection (Cloudflare, Akamai, Imperva, DataDome)</p>
<p>- Multi-layer bypass (cloudscraper, proxy rotation)</p>
<p>- Fingerprint rotation (50+ profiles)</p>
<p>- Async support via asyncio.to_thread for M1 compatibility</p>
</div>
</details>
</li>
<li><code>HeaderSpoofer</code> (_models.py)
<details><summary>Manages HTTP header spoofing configurations.</summary>
<div class="doc-comment">
<p>Manages HTTP header spoofing configurations.</p>
<p></p>
<p>M1 8GB Optimized: Slots pattern for memory efficiency.</p>
</div>
</details>
</li>
<li><code>TorProxyManager</code> (_models.py) — <span class="doc-comment-inline">Check if Tor SOCKS proxy is running on port 9050.</span></li>
<li><code>MonitoredSource</code> (_models.py)
<details><summary>Configuration for a monitored source.</summary>
<div class="doc-comment">
<p>Configuration for a monitored source.</p>
<p></p>
<p>M1 8GB Optimized: Minimal memory footprint, uses slots pattern internally.</p>
</div>
</details>
</li>
<li><code>FingerprintProfile</code> (_models.py)
<details><summary>TLS/HTTP fingerprint profile for stealth scraping.</summary>
<div class="doc-comment">
<p>TLS/HTTP fingerprint profile for stealth scraping.</p>
<p></p>
<p>M1 8GB Optimized: Slots pattern, immutable after creation.</p>
</div>
</details>
</li>
<li><code>AlertRule</code> (_models.py)
<details><summary>Rule for generating alerts based on monitoring events.</summary>
<div class="doc-comment">
<p>Rule for generating alerts based on monitoring events.</p>
<p></p>
<p>M1 8GB Optimized: Slots pattern, no dynamic attributes.</p>
</div>
</details>
</li>
<li><code>StreamEvent</code> (_models.py) — <span class="doc-comment-inline">Represents a change detection event from the monitoring stream.</span></li>
<li><code>ProtectionType</code> (_models.py) — <span class="doc-comment-inline">Types of anti-bot protection</span></li>
<li><code>ScrapingResult</code> (_models.py) — <span class="doc-comment-inline">Result of a scraping operation.</span></li>
<li><code>Alert</code> (_models.py) — <span class="doc-comment-inline">Represents an alert generated from monitoring.</span></li>
<li><code>BypassMethod</code> (_models.py) — <span class="doc-comment-inline">Methods to bypass anti-bot protection</span></li>
<li><code>ProxyConfig</code> (_models.py) — <span class="doc-comment-inline">Configuration for proxy rotation.</span></li>
<li><code>SearchResult</code> (_models.py) — <span class="doc-comment-inline">Represents a single search result from stealth search.</span></li>
<li><code>Severity</code> (_models.py) — <span class="doc-comment-inline">Alert severity levels</span></li>
<li><code>ChangeType</code> (_models.py) — <span class="doc-comment-inline">Types of content changes detected</span></li>
<li><code>Change</code> (_models.py) — <span class="doc-comment-inline">Represents a single detected change</span></li>
<li><code>HeaderConfig</code> (_models.py) — <span class="doc-comment-inline">Configuration for HTTP header spoofing.</span></li>
<li><code>SourceType</code> (_models.py) — <span class="doc-comment-inline">Types of monitored sources</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (48)</summary>
<ul>
<li><code>_check_source</code> (monitor.py)
<details><summary>Check a single source for changes.</summary>
<div class="doc-comment">
<p>Check a single source for changes.</p>
<p></p>
<p>M1 8GB Optimized:</p>
<p>- Uses HEAD request first to check if content changed</p>
<p>- Connection reuse via session</p>
<p>- Minimal memory allocation</p>
</div>
</details>
</li>
<li><code>scrape</code> (scraper.py)
<details><summary>Scrape a URL with protection detection and bypass.</summary>
<div class="doc-comment">
<p>Scrape a URL with protection detection and bypass.</p>
<p></p>
<p>Args:</p>
<p>url: URL to scrape</p>
<p>**kwargs: Additional options (headers, proxies, etc.)</p>
<p></p>
<p>Returns:</p>
<p>ScrapingResult with content or error</p>
</div>
</details>
</li>
<li><code>_detect_changes</code> (monitor.py)</li>
<li><code>search</code> (scraper.py)</li>
<li><code>get_headers</code> (_models.py)</li>
<li><code>_head_check_changed</code> (monitor.py)
<details><summary>Use HEAD request to check if content changed.</summary>
<div class="doc-comment">
<p>Use HEAD request to check if content changed.</p>
<p></p>
<p>M1 8GB Optimization: Avoids downloading full content if not needed.</p>
</div>
</details>
</li>
<li><code>_extract_entities</code> (monitor.py) — <span class="doc-comment-inline">Extract entities from content using simple patterns.</span></li>
<li><code>_parse_duckduckgo</code> (scraper.py)</li>
<li><code>_detect_protection</code> (scraper.py)</li>
<li><code>_monitor_loop</code> (monitor.py) — <span class="doc-comment-inline">Main monitoring loop - M1 8GB optimized.</span></li>
<li><code>_fetch_html</code> (scraper.py)
<details><summary>Fetch HTML using available library with subprocess curl fallback (Sprint 8R).</summary>
<div class="doc-comment">
<p>Fetch HTML using available library with subprocess curl fallback (Sprint 8R).</p>
<p></p>
<p>Bounded fix TICKET-004: Never use subprocess on M1 (tickover only).</p>
</div>
</details>
</li>
<li><code>_parse_brave</code> (scraper.py) — <span class="doc-comment-inline">Parse Brave Search HTML results (Sprint 8R).</span></li>
<li><code>_parse_google</code> (scraper.py) — <span class="doc-comment-inline">Parse Google HTML results.</span></li>
<li><code>_process_event</code> (monitor.py) — <span class="doc-comment-inline">Process a stream event - generate alerts and store.</span></li>
<li><code>_determine_severity</code> (monitor.py)</li>
<li><code>_load_fingerprint_profiles</code> (scraper.py) — <span class="doc-comment-inline">Load TLS/HTTP fingerprint profiles for rotation.</span></li>
<li><code>_fetch_content</code> (scraper.py)</li>
<li><code>add_source</code> (monitor.py)
<details><summary>Add a source to monitor.</summary>
<div class="doc-comment">
<p>Add a source to monitor.</p>
<p></p>
<p>Args:</p>
<p>source: MonitoredSource configuration</p>
<p></p>
<p>Returns:</p>
<p>True if source was added successfully</p>
</div>
</details>
</li>
<li><code>_fetch_rss</code> (monitor.py) — <span class="doc-comment-inline">Fetch and parse RSS/Atom feed using selectolax (async-native).</span></li>
<li><code>_search_duckduckgo</code> (scraper.py)</li>
<li><code>_search_brave</code> (scraper.py)</li>
<li><code>_search_google</code> (scraper.py)</li>
<li><code>is_running</code> (_models.py) — <span class="doc-comment-inline">Return True if Tor SOCKS port 9050 is reachable (cached, 5s TTL).</span></li>
<li><code>__init__</code> (monitor.py)</li>
<li><code>_fetch_url</code> (monitor.py) — <span class="doc-comment-inline">Fetch a general URL.</span></li>
<li><code>_fetch_with_curl_cffi_async</code> (scraper.py)</li>
<li><code>_check_dependencies</code> (scraper.py) — <span class="doc-comment-inline">Check for available HTTP libraries.</span></li>
<li><code>_fetch_with_curl_cffi</code> (scraper.py)</li>
<li><code>_check_dependencies</code> (scraper.py) — <span class="doc-comment-inline">Check for available libraries.</span></li>
<li><code>_fetch_api</code> (monitor.py) — <span class="doc-comment-inline">Fetch from API endpoint.</span></li>
<li><code>remove_source</code> (monitor.py)
<details><summary>Remove a source from monitoring.</summary>
<div class="doc-comment">
<p>Remove a source from monitoring.</p>
<p></p>
<p>Args:</p>
<p>source_id: ID of source to remove</p>
<p></p>
<p>Returns:</p>
<p>True if source was removed</p>
</div>
</details>
</li>
<li><code>__init__</code> (scraper.py)</li>
<li><code>_fetch_with_cloudscraper</code> (scraper.py)</li>
<li><code>_fetch_with_httpx_fallback</code> (scraper.py)</li>
<li><code>_check_source_with_semaphore</code> (monitor.py)</li>
<li><code>start_monitoring</code> (monitor.py) — <span class="doc-comment-inline">Start the monitoring loop.</span></li>
<li><code>_cleanup_memory</code> (monitor.py) — <span class="doc-comment-inline">Periodic memory cleanup for M1 8GB optimization.</span></li>
<li><code>_fetch_with_httpx</code> (scraper.py) — <span class="doc-comment-inline">Fetch using httpx.Client (fallback).</span></li>
<li><code>stop_monitoring</code> (monitor.py) — <span class="doc-comment-inline">Stop the monitoring loop.</span></li>
<li><code>initialize</code> (monitor.py) — <span class="doc-comment-inline">Initialize the monitor with HTTP session.</span></li>
<li><code>_get_default_headers</code> (monitor.py) — <span class="doc-comment-inline">Get default HTTP headers for monitoring.</span></li>
<li><code>_check_dependencies</code> (monitor.py) — <span class="doc-comment-inline">Check for optional dependencies.</span></li>
<li><code>__init__</code> (scraper.py)</li>
<li><code>__init__</code> (_models.py)</li>
<li><code>_match_keywords</code> (monitor.py)</li>
<li><code>__post_init__</code> (_models.py) — <span class="doc-comment-inline">Validate source configuration</span></li>
<li><code>_calculate_hash</code> (monitor.py) — <span class="doc-comment-inline">Calculate SHA256 hash of content.</span></li>
<li><code>_generate_id</code> (monitor.py) — <span class="doc-comment-inline">Generate a unique ID.</span></li>
</ul>
</details>

<details><summary><strong>Constant</strong> (7)</summary>
<ul>
<li><code>_CRAWL_BLOOM_KEY</code> (_models.py)</li>
<li><code>_PATCHED_SURFACES</code> (_models.py)</li>
<li><code>_UNPATCHED_SURFACES</code> (_models.py)</li>
<li><code>_CRAWL_BLOOM</code> (_models.py)</li>
<li><code>_CRAWL_BLOOM_PATH_A</code> (_models.py)</li>
<li><code>_CRAWL_BLOOM_PATH_B</code> (_models.py)</li>
<li><code>_CRAWL_BLOOM_CAPACITY</code> (_models.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 4 |
| Total lines | 1749 |
| Avg lines/file | 437 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

