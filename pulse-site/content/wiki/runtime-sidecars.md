+++
title = "runtime/sidecars/"
weight = 22
description = "<think> Let me analyze the structural context for the `runtime/sidecars` module and write an appropriate overview.  Key observations: 1. The module has 15 Python files with 486 total lines (average 32..."

[extra]
tier = 2
file_count = 15
total_lines = 486
languages = "Python"
parent_path = "runtime"
+++

<think>
Let me analyze the structural context for the `runtime/sidecars` module and write an appropriate overview.

Key observations:
1. The module has 15 Python files with 486 total lines (average 32 lines/file)
2. It has three sub-modules: discovery, enrichment, and forensics
3. It has no outgoing or incoming dependencies detected
4. The key abstraction is `SchedulerBackedSidecarAdapter` - an adapter pattern that delegates to Sprint Scheduler
5. It has a `run_async` function referenced in 8 files and `__getattr__` in the root referenced in 113 files
6. This is a relatively small module (Tier 2) with 11 classes representing different sidecar adapters
7. The adapters cover discovery (DHT, IPFS, Onion, I2P, Common Crawl), enrichment (BGP, Banner, TI Feed), and forensics (Digital Ghost, Steganography)
8. It uses a lazy loading pattern with `_CACHE` and `_LOADED` constants
9. The module serves as a bridge/extension mechanism for the Sprint Scheduler

The module appears to be a plugin-like system that provides various discovery, enrichment, and forensic capabilities as sidecars that delegate to a scheduler. It's architecturally a bridge between the main system and various external data sources/network capabilities.

Important: 
- I should NOT start with "The X module consists of..." 
- I should state what the module DOES first
- I should describe its architectural role
- I should note complexity/scale
- I should NOT enumerate specific file names
- I should vary sentence structure
- 4-8 sentences
- No missing spaces

Let me write this:
</think>

This module provides a collection of pluggable sidecar adapters that extend the system's capabilities across discovery, enrichment, and forensic analysis domains. Each adapter follows a delegation pattern—wrapping calls to a central `SprintScheduler` so that functionality can be invoked asynchronously and managed through a unified registry. The module acts as a bridge between the core scheduler and external data sources such as DHT networks, IPFS, threat intelligence feeds, BGP routing data, and dark web endpoints, allowing these capabilities to be composed without tight coupling. With 11 adapter classes distributed across just 486 lines of Python, the implementation is deliberately lightweight and focused on wiring rather than heavy logic. A lazy-loading mechanism with per-submodule caches enables the sidecars to be imported without triggering expensive initialization. The module occupies a Tier 2 position with no external dependencies, suggesting it is consumed by higher-level orchestration components rather than foundational services. Its design prioritizes observability over silent failures—missing scheduler methods produce logged warnings and empty results rather than crashes, making gaps in implementation discoverable through the registry.

## Structure

### Sub-modules

- [**discovery/**](/wiki/runtime-sidecars-discovery/) — 6 files, 145 lines (Python)
- [**enrichment/**](/wiki/runtime-sidecars-enrichment/) — 4 files, 91 lines (Python)
- [**forensics/**](/wiki/runtime-sidecars-forensics/) — 3 files, 67 lines (Python)

| Language | Files |
|---|---|
| Python | 15 |

### Directories

| Directory | Files | Lines |
|---|---|---|
| discovery/ | 6 | 145 |
| enrichment/ | 4 | 91 |
| forensics/ | 3 | 67 |

### Largest Files

- `_base.py` (108 lines)
- `__init__.py` (75 lines)
- `discovery/__init__.py` (45 lines)
- `enrichment/__init__.py` (38 lines)
- `forensics/__init__.py` (35 lines)
- `discovery/_dht.py` (23 lines)
- `discovery/_commoncrawl.py` (23 lines)
- `discovery/_ipfs.py` (22 lines)
- `enrichment/_ti_feed.py` (21 lines)
- `discovery/_onion.py` (16 lines)

<details><summary><strong>Show 5 more files</strong></summary>

- `discovery/_i2p.py` (16 lines)
- `forensics/_digital_ghost.py` (16 lines)
- `forensics/_steganography.py` (16 lines)
- `enrichment/_banner.py` (16 lines)
- `enrichment/_bgp.py` (16 lines)

</details>


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>__init__</code> (Function) in _base.py — referenced in 641 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _body_hash.py, _buffer.py, _catalog.py +592 more</li></ul>
</li>
<li>
<p><code>__getattr__</code> (Function) in __init__.py — referenced in 113 files</p>
<ul><li class="ref-list">Referenced by: _domain_protocol.py, _lazy_imports.py, _telemetry_setup.py, academic_discovery.py, academic_search.py +83 more</li></ul>
</li>
<li>
<p><code>SchedulerBackedSidecarAdapter</code> (Class) in _base.py — referenced in 12 files</p>
<details><summary>Adapter that delegates to an existing `SprintScheduler` private method.</summary>
<div class="doc-comment">
<p>Adapter that delegates to an existing `SprintScheduler` private method.</p>
<p></p>
<p>Subclasses set `scheduler_method_name` to the method to invoke.</p>
<p>`run_async` is a thin `getattr` + `await` — all error handling lives in</p>
<p>`BaseSidecarAdapter.run` (fail-soft at registry level).</p>
<p></p>
<p>Invariant: scheduler_method_name must be a coroutine function on</p>
<p>SprintScheduler. If the method is missing (e.g. `commoncrawl` /</p>
<p>`ti_feed` not yet implemented), the adapter logs once and returns</p>
<p>an empty finding list. This makes the previous silent no-op behavior</p>
<p>observable and documentable.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: _banner.py, _bgp.py, _commoncrawl.py, _dht.py, _digital_ghost.py +6 more</li></ul>
</li>
<li>
<p><code>run_async</code> (Function) in _base.py — referenced in 8 files</p>
<ul><li class="ref-list">Referenced by: isolated_executors.py, sidecar_adapter.py, sidecar_orchestrator.py, sidecar_protocol.py, sidecar_protocol_adapters.py +2 more</li></ul>
</li>
<li>
<p><code>BGPEnrichmentSidecarAdapter</code> (Class) in _bgp.py — referenced in 4 files</p>
<details><summary>F229: BGP enrichment — AS path analysis for IP/ASN in query.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, sidecar_legacy_adapters.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (7)</summary>
<ul>
<li><code>run_async</code> (_base.py)</li>
<li><code>__getattr__</code> (__init__.py)</li>
<li><code>__getattr__</code> (__init__.py)</li>
<li><code>__getattr__</code> (__init__.py)</li>
<li><code>__getattr__</code> (__init__.py)</li>
<li><code>bind_scheduler</code> (_base.py)
<details><summary>Bind the live SprintScheduler instance for the current async context.</summary>
<div class="doc-comment">
<p>Bind the live SprintScheduler instance for the current async context.</p>
<p></p>
<p>Called by `SidecarOrchestrator.__init__`. Idempotent. Pass `None` to</p>
<p>clear (used in tests + teardown).</p>
</div>
</details>
</li>
<li><code>__init__</code> (_base.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (11)</summary>
<ul>
<li><code>SchedulerBackedSidecarAdapter</code> (_base.py)
<details><summary>Adapter that delegates to an existing `SprintScheduler` private method.</summary>
<div class="doc-comment">
<p>Adapter that delegates to an existing `SprintScheduler` private method.</p>
<p></p>
<p>Subclasses set `scheduler_method_name` to the method to invoke.</p>
<p>`run_async` is a thin `getattr` + `await` — all error handling lives in</p>
<p>`BaseSidecarAdapter.run` (fail-soft at registry level).</p>
<p></p>
<p>Invariant: scheduler_method_name must be a coroutine function on</p>
<p>SprintScheduler. If the method is missing (e.g. `commoncrawl` /</p>
<p>`ti_feed` not yet implemented), the adapter logs once and returns</p>
<p>an empty finding list. This makes the previous silent no-op behavior</p>
<p>observable and documentable.</p>
</div>
</details>
</li>
<li><code>DHTDiscoverySidecarAdapter</code> (_dht.py)
<details><summary>F214Q: DHT torrent discovery via BitTorrent DHT network.</summary>
<div class="doc-comment">
<p>F214Q: DHT torrent discovery via BitTorrent DHT network.</p>
<p></p>
<p>Coexists with `DHTSidecarAdapter` (F350M-R) which uses</p>
<p>`discovery.dht_adapter.DHTAdapter`. The two paths are independent —</p>
<p>this one delegates to the scheduler's pre-existing implementation</p>
<p>which has its own Kademlia client wiring. Future work: pick one as</p>
<p>canonical and deprecate the other.</p>
</div>
</details>
</li>
<li><code>CommonCrawlSidecarAdapter</code> (_commoncrawl.py)
<details><summary>F250F: CommonCrawl CDX domain discovery.</summary>
<div class="doc-comment">
<p>F250F: CommonCrawl CDX domain discovery.</p>
<p></p>
<p>Placeholder — the scheduler method `_run_commoncrawl_sidecar` is not</p>
<p>yet implemented (previously called from `sidecar_orchestrator` via</p>
<p>`getattr` and silently no-op'd). This adapter documents the gap and</p>
<p>makes it observable in `SidecarRegistry.get_all_registered()` output.</p>
</div>
</details>
</li>
<li><code>IPFSDiscoverySidecarAdapter</code> (_ipfs.py)
<details><summary>F229: IPFS discovery — fetch unindexed content from IPFS network.</summary>
<div class="doc-comment">
<p>F229: IPFS discovery — fetch unindexed content from IPFS network.</p>
<p></p>
<p>Note: the previous `sidecar_orchestrator._run_ipfs_discovery_sidecar`</p>
<p>wrapper called `_run_ipfs_enrichment_sidecar` on the scheduler — a typo</p>
<p>that caused silent no-op execution. This adapter binds the CORRECT</p>
<p>method name, restoring IPFS discovery functionality.</p>
</div>
</details>
</li>
<li><code>TIFeedSidecarAdapter</code> (_ti_feed.py)
<details><summary>F252: Threat intelligence feed advisory (NVD + CISA KEV).</summary>
<div class="doc-comment">
<p>F252: Threat intelligence feed advisory (NVD + CISA KEV).</p>
<p></p>
<p>Fetches structured TI feeds (NVD API + CISA KEV catalog) in parallel</p>
<p>via SprintScheduler._run_ti_feed_sidecar(). Registered adapters are</p>
<p>dispatched with safe_gather_ok for bounded concurrent execution.</p>
</div>
</details>
</li>
<li><code>OnionDiscoverySidecarAdapter</code> (_onion.py) — <span class="doc-comment-inline">F251: Dark web .onion discovery via Tor transport.</span></li>
<li><code>I2PDiscoverySidecarAdapter</code> (_i2p.py) — <span class="doc-comment-inline">F2P: I2P .i2p discovery via I2P transport.</span></li>
<li><code>DigitalGhostSidecarAdapter</code> (_digital_ghost.py) — <span class="doc-comment-inline">F3FORENSICS: Digital ghost detection on file artifacts.</span></li>
<li><code>SteganographySidecarAdapter</code> (_steganography.py) — <span class="doc-comment-inline">F3FORENSICS: Steganography detection on image artifacts.</span></li>
<li><code>BannerGrabSidecarAdapter</code> (_banner.py) — <span class="doc-comment-inline">F229: TCP banner enumeration for service fingerprinting.</span></li>
<li><code>BGPEnrichmentSidecarAdapter</code> (_bgp.py) — <span class="doc-comment-inline">F229: BGP enrichment — AS path analysis for IP/ASN in query.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (2)</summary>
<ul>
<li><code>run_async</code> (_base.py)</li>
<li><code>__init__</code> (_base.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (8)</summary>
<ul>
<li><code>_CACHE</code> (__init__.py)</li>
<li><code>_LOADED</code> (__init__.py)</li>
<li><code>_CACHE</code> (__init__.py)</li>
<li><code>_LOADED</code> (__init__.py)</li>
<li><code>_CACHE</code> (__init__.py)</li>
<li><code>_LOADED</code> (__init__.py)</li>
<li><code>_CACHE</code> (__init__.py)</li>
<li><code>_LOADED</code> (__init__.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 15 |
| Total lines | 486 |
| Avg lines/file | 32 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

