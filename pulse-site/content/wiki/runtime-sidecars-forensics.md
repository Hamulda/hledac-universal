+++
title = "runtime/sidecars/forensics/"
weight = 25
description = "<think> Let me analyze the module structure:  1. The module is `runtime/sidecars/forensics` - a Python module 2. It has 3 files, 67 total lines (very small/lean module) 3. No outgoing dependencies 4. ..."

[extra]
tier = 2
file_count = 3
total_lines = 67
languages = "Python"
parent_path = "runtime"
+++

<think>
Let me analyze the module structure:

1. The module is `runtime/sidecars/forensics` - a Python module
2. It has 3 files, 67 total lines (very small/lean module)
3. No outgoing dependencies
4. No incoming dependencies (isolated)
5. Key symbols:
 - `__getattr__` is referenced in 113 files (high fan-out within the module's own context)
 - `_LOADED` referenced in 5 files (conftest.py)
 - `DigitalGhostSidecarAdapter` - referenced in 4 files, provides digital ghost detection on file artifacts
 - `SteganographySidecarAdapter` - referenced in 4 files, provides steganography detection on image artifacts
 - `_CACHE` referenced in 4 files

The module provides forensics capabilities for detecting:
- Digital ghosts on file artifacts
- Steganography on image artifacts

It has two sidecar adapter classes that seem to be the core abstractions. The module is very small (67 lines) and has no dependencies - it's self-contained.

The `__getattr__` function being referenced in 113 files suggests a lazy loading pattern or a module-level entry point that many other modules use.

This appears to be a leaf/utility module providing specialized forensics detection capabilities. It's isolated (no deps) but has moderate internal references.

Let me write a module overview:
- First sentence: what the module does/purposes
- Purpose: forensics detection (digital ghost and steganography)
- Architectural role: small, isolated module with sidecar adapters
- Scale: tiny (67 lines, 3 files)
- Fan-out: moderate internal references

I should NOT start with "The X module consists of..." or "This module contains..."
</think>

This module provides specialized forensics detection capabilities, exposing sidecar adapters that detect digital ghosts within file artifacts and steganographic content hidden in image files. As a tier 2 module with zero external dependencies, it operates as a self-contained leaf component—its small footprint (67 lines across 3 Python files) reflects a focused, single-responsibility design. The central entry point uses lazy attribute loading, a pattern referenced across more than a hundred files in the codebase, suggesting that other modules consume this module's detection services through a lightweight dynamic import mechanism. Two adapter classes form the core abstractions: one for digital ghost detection and another for steganography analysis, both instantiated and used via legacy adapter integrations. Despite being isolated from the dependency graph, the module's detection functions feed into broader artifact processing workflows, making it a specialized utility that other systems rely on for security and integrity verification.

## Structure

| Language | Files |
|---|---|
| Python | 3 |

### Largest Files

- `__init__.py` (35 lines)
- `_digital_ghost.py` (16 lines)
- `_steganography.py` (16 lines)


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
<p><code>DigitalGhostSidecarAdapter</code> (Class) in _digital_ghost.py — referenced in 4 files</p>
<details><summary>F3FORENSICS: Digital ghost detection on file artifacts.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, sidecar_legacy_adapters.py</li></ul>
</li>
<li>
<p><code>SteganographySidecarAdapter</code> (Class) in _steganography.py — referenced in 4 files</p>
<details><summary>F3FORENSICS: Steganography detection on image artifacts.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, sidecar_legacy_adapters.py</li></ul>
</li>
<li>
<p><code>_CACHE</code> (Constant) in __init__.py — referenced in 4 files</p>
</li>
</ul>

<details><summary><strong>Function</strong> (1)</summary>
<ul>
<li><code>__getattr__</code> (__init__.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (2)</summary>
<ul>
<li><code>DigitalGhostSidecarAdapter</code> (_digital_ghost.py) — <span class="doc-comment-inline">F3FORENSICS: Digital ghost detection on file artifacts.</span></li>
<li><code>SteganographySidecarAdapter</code> (_steganography.py) — <span class="doc-comment-inline">F3FORENSICS: Steganography detection on image artifacts.</span></li>
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
| Files | 3 |
| Total lines | 67 |
| Avg lines/file | 22 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

