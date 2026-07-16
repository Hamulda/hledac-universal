+++
title = "runtime/sidecars/enrichment/"
weight = 24
description = "<think> Let me analyze this module carefully based on the structural context provided.  The module is `runtime/sidecars/enrichment` and it contains: - 4 Python files with 91 total lines (averaging 22 ..."

[extra]
tier = 2
file_count = 4
total_lines = 91
languages = "Python"
parent_path = "runtime"
+++

<think>
Let me analyze this module carefully based on the structural context provided.

The module is `runtime/sidecars/enrichment` and it contains:
- 4 Python files with 91 total lines (averaging 22 lines per file)
- No outgoing dependencies
- No incoming dependencies
- Key symbols: __getattr__, _LOADED, and three adapter classes (TIFeed SidecarAdapter, Banner GrabSidecar Adapter, BGPEnrichment SidecarAdapter)

The module appears to provide enrichment services:
1. TIFeed SidecarAdapter - threat intelligence feed advisory (NVD + CISA KEV)
2. Banner GrabSidecar Adapter - TCP banner enumeration for service fingerprinting
3. BGPEnrichment SidecarAdapter - BGP enrichment for AS path analysis

This module is interesting because:
- It's Tier 2 (no outgoing deps, no incoming deps detected - but wait, the __getattr__ is referenced in 113 files!)
- The __getattr__ function is referenced in 113 files - this is a very high fan-out
- It has 0 incoming dependencies detected, but clearly many files reference __getattr__

Wait, looking more carefully: the __getattr__ is referenced by 113 files, and _LOADED is referenced by conftest.py. The three adapters are referenced in __init__.py and sidecar_legacy_adapters.py.

This suggests the module provides lazy-loading adapters for enrichment sidecars. The __getattr__ being referenced in 113 files is unusual - this might be a module-level __getattr__ that enables lazy importing of the adapter classes.

The module:
- Does NOT have outgoing dependencies
- Does NOT appear to have incoming dependencies (from the dependency graph perspective)
- BUT the __getattr__ function is referenced by 113 files

This is a leaf module in terms of dependencies but appears to be a hub in terms of actual usage through __getattr__ lazy loading.


The adapters are quite specialized and narrow in scope, each focusing on a specific enrichment function with minimal implementation complexity. They leverage lazy loading to provide a flexible, lightweight approach to enrichment sidecar functionality without introducing heavy dependencies or complex abstractions.
</think>

This module provides lazy-loaded adapter classes for external enrichment sidecars, including threat intelligence feed integration (NVD and CISA KEV), TCP banner enumeration for service fingerprinting, and BGP enrichment for AS path analysis of IP addresses and ASNs. The module is notably compact at 91 lines across four files with no external dependencies, acting as a thin facade that defers actual sidecar implementation loading until runtime through a module-level `__getattr__` function. This lazy-loading mechanism is referenced by over a hundred other files in the codebase, indicating widespread indirect usage across the system while maintaining a negligible code footprint. The three adapter types—threat intelligence feeds, banner grabbing, and BGP analysis—represent distinct enrichment capabilities that support security and network analysis workflows, though the actual sidecar logic appears to reside elsewhere. Changes to this module carry minimal blast radius given its isolated position, yet its role as an import gateway means disruptions could affect many dependent modules that rely on lazy loading behavior.

## Structure

| Language | Files |
|---|---|
| Python | 4 |

### Largest Files

- `__init__.py` (38 lines)
- `_ti_feed.py` (21 lines)
- `_banner.py` (16 lines)
- `_bgp.py` (16 lines)


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
<p><code>BGPEnrichmentSidecarAdapter</code> (Class) in _bgp.py — referenced in 4 files</p>
<details><summary>F229: BGP enrichment — AS path analysis for IP/ASN in query.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, sidecar_legacy_adapters.py</li></ul>
</li>
<li>
<p><code>BannerGrabSidecarAdapter</code> (Class) in _banner.py — referenced in 4 files</p>
<details><summary>F229: TCP banner enumeration for service fingerprinting.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, sidecar_legacy_adapters.py</li></ul>
</li>
<li>
<p><code>TIFeedSidecarAdapter</code> (Class) in _ti_feed.py — referenced in 4 files</p>
<details><summary>F252: Threat intelligence feed advisory (NVD + CISA KEV).</summary>
<div class="doc-comment">
<p>F252: Threat intelligence feed advisory (NVD + CISA KEV).</p>
<p></p>
<p>Fetches structured TI feeds (NVD API + CISA KEV catalog) in parallel</p>
<p>via SprintScheduler._run_ti_feed_sidecar(). Registered adapters are</p>
<p>dispatched with safe_gather_ok for bounded concurrent execution.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, sidecar_legacy_adapters.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (1)</summary>
<ul>
<li><code>__getattr__</code> (__init__.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (3)</summary>
<ul>
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
<li><code>BannerGrabSidecarAdapter</code> (_banner.py) — <span class="doc-comment-inline">F229: TCP banner enumeration for service fingerprinting.</span></li>
<li><code>BGPEnrichmentSidecarAdapter</code> (_bgp.py) — <span class="doc-comment-inline">F229: BGP enrichment — AS path analysis for IP/ASN in query.</span></li>
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
| Files | 4 |
| Total lines | 91 |
| Avg lines/file | 22 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

