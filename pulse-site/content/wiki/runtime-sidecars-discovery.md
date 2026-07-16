+++
title = "runtime/sidecars/discovery/"
weight = 23
description = "<think> Let me analyze this module carefully.  The module is `runtime/sidecars/discovery` and based on the context:  1. **Purpose**: This module provides adapter classes for various distributed/networ..."

[extra]
tier = 2
file_count = 6
total_lines = 145
languages = "Python"
parent_path = "runtime"
+++

<think>
Let me analyze this module carefully.

The module is `runtime/sidecars/discovery` and based on the context:

1. **Purpose**: This module provides adapter classes for various distributed/network discovery mechanisms (DHT, IPFS, I2P, Onion/Tor, Common Crawl). It serves as a bridge between the sidecar orchestration system and various network-specific discovery protocols.

2. **Architecture role**: It appears to be a leaf module (no outgoing dependencies) but has significant incoming dependencies through the `__getattr__` function which is referenced in 113 files. This is interesting - it's a hub in terms of being accessed frequently via dynamic imports.

3. **Scale**: Small module - 6 files, 145 lines total, average 24 lines per file. Very lean.

4. **Key patterns**:
 - Uses lazy loading via `__getattr__` (referenced 113 times)
 - Contains adapter classes for different network protocols
 - Some adapters are placeholders (Common Crawl) or fixes (IPFS)
 - Coexists with other similar adapters (DHT has two implementations)

5. **What it provides**: Discovery sidecar adapters for decentralized networks - DHT (BitTorrent), IPFS, I2P, Tor (.onion), and Common Crawl.

6. **Interesting notes from the docs**:
 - IPFS adapter fixes a typo that caused silent no-ops
 - Common Crawl is a placeholder with unimplemented scheduler method
 - DHT has two paths that need consolidation

Now I need to write a module overview that:
- First sentence: states what the module DOES (not what it contains)
- Describes purpose, responsibilities, architectural role
- Notes if it's a hub/leaf/bridge
- Explains dependencies
- Notes complexity and scale
- 4-8 sentences
- No file/class enumeration unless central
- Varied sentence structure


- Do NOT speculate
- Proofread for missing spaces

The module acts as a discovery protocol abstraction layer, exposing multiple network discovery mechanisms through a unified adapter interface. Its primary function is providing lazy-loaded access to decentralized network discovery capabilities via a single entry point, with adapters handling protocol-specific nuances. Despite minimal code, it serves as a critical dependency hub—dynamically imported across 113 files to enable cross-network content discovery without tight coupling.
</think>

This module provides lazy-loaded adapter classes that bridge the sidecar orchestration system to various decentralized network discovery mechanisms, enabling discovery across DHT (BitTorrent), IPFS, I2P, Tor (.onion), and Common Crawl protocols. The primary entry point is a `__getattr__` function that is referenced by 113 files throughout the codebase, making this a high-fan-out dependency hub despite its compact implementation of just 145 lines across 6 files. Each adapter handles protocol-specific wiring—including corrections for previously silent failures like an IPFS adapter that bound the correct scheduler method after a typo caused no-op execution, and a Common Crawl adapter that serves as an observable placeholder for an unimplemented method. The module consumes no external dependencies, acting as a pure interface layer that delegates to scheduler implementations, though the DHT adapter coexists with a parallel implementation and documentation flags this as a consolidation opportunity. Its small footprint (averaging 24 lines per file) reflects focused responsibility, yet changes here carry wide blast radius given the extensive dynamic imports relying on its attribute-based loading pattern.

## Structure

| Language | Files |
|---|---|
| Python | 6 |

### Largest Files

- `__init__.py` (45 lines)
- `_dht.py` (23 lines)
- `_commoncrawl.py` (23 lines)
- `_ipfs.py` (22 lines)
- `_onion.py` (16 lines)
- `_i2p.py` (16 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>__getattr__</code> (Function) in __init__.py — referenced in 113 files</p>
<ul><li class="ref-list">Referenced by: _domain_protocol.py, _lazy_imports.py, _telemetry_setup.py, academic_discovery.py, academic_search.py +83 more</li></ul>
</li>
<li>
<p><code>_LOADED</code> (Constant) in __init__.py — referenced in 5 files</p>
<ul><li class="ref-list">Referenced by: conftest.py</li></ul>
</li>
<li>
<p><code>CommonCrawlSidecarAdapter</code> (Class) in _commoncrawl.py — referenced in 4 files</p>
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
<ul><li class="ref-list">Referenced by: __init__.py, sidecar_legacy_adapters.py</li></ul>
</li>
<li>
<p><code>DHTDiscoverySidecarAdapter</code> (Class) in _dht.py — referenced in 4 files</p>
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
<ul><li class="ref-list">Referenced by: __init__.py, sidecar_legacy_adapters.py</li></ul>
</li>
<li>
<p><code>I2PDiscoverySidecarAdapter</code> (Class) in _i2p.py — referenced in 4 files</p>
<details><summary>F2P: I2P .i2p discovery via I2P transport.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, sidecar_legacy_adapters.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (1)</summary>
<ul>
<li><code>__getattr__</code> (__init__.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (5)</summary>
<ul>
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
<li><code>OnionDiscoverySidecarAdapter</code> (_onion.py) — <span class="doc-comment-inline">F251: Dark web .onion discovery via Tor transport.</span></li>
<li><code>I2PDiscoverySidecarAdapter</code> (_i2p.py) — <span class="doc-comment-inline">F2P: I2P .i2p discovery via I2P transport.</span></li>
</ul>
</details>

<details><summary><strong>Constant</strong> (2)</summary>
<ul>
<li><code>_CACHE</code> (__init__.py)</li>
<li><code>_LOADED</code> (__init__.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 6 |
| Total lines | 145 |
| Avg lines/file | 24 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

