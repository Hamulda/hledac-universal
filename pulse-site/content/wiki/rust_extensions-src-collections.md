+++
title = "rust_extensions/src/collections/"
weight = 28
description = "<think> Let me analyze the structural context to write a module overview for `rust_extensions/src/collections`.  Key information: - This is a small module with only 3 files (172 total lines, ~57 lines..."

[extra]
tier = 2
file_count = 3
total_lines = 172
languages = "Python, Rust"
parent_path = "rust_extensions"
has_mermaid = true
+++

<think>
Let me analyze the structural context to write a module overview for `rust_extensions/src/collections`.

Key information:
- This is a small module with only 3 files (172 total lines, ~57 lines/file average)
- Contains Rust code for a Ring Buffer data structure with Python bindings
- The Ring Buffer is a bounded buffer holding IOC dicts as Python objects
- It has no outgoing dependencies but is used by 1 file (lib.rs)
- High fan-in on certain functions: `clear` is referenced in 242 files, `capacity` in 93 files, `is_empty` in 70 files, `push` in 51 files
- This indicates the module is quite widely used despite being small
- It's a leaf module (no dependents from other modules, but it's used by lib.rs)
- The module bridges Rust and Python via FFI

The module provides:
- A Ring Buffer data structure for holding IOC (Indicators of Compromise) entries
- Bounded storage with automatic eviction of oldest entries
- Python bindings for all operations
- Functions: push, get_recent, get_all, len, is_empty, capacity, clear, with_capacity, __repr__

Architecture role:
- This is a bridge between Rust and Python
- It's a utility module providing a specific data structure
- Despite being small, it has high fan-out usage (many references to its functions across the codebase)
- Changes would have wide blast radius due to high references

Let me write the overview:
</think>

This module provides a bounded ring buffer for storing Python IOC dictionaries with automatic oldest-entry eviction, serving as a bridge between Rust's performance-critical data structures and Python consumption. With just 172 lines across 3 files, it exposes a focused API—`push`, `get_recent`, `get_all`, `len`, `is_empty`, `capacity`, and `clear`—that integrates seamlessly into Python via FFI. The Ring Buffer maintains its backing storage allocation even when cleared, preserving capacity for reuse. Although a leaf module with no upstream dependencies, it exhibits extremely high fan-out: the `clear` function alone is referenced by 242 files across the codebase, making this compact module a critical touchpoint whose changes would have a wide blast radius. The default capacity of 200 entries supports hypothesis feedback loops by retaining recent IOCs while silently discarding older ones to prevent unbounded memory growth.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_rust_extensions_src_collections["<b>rust_extensions/src/collections/</b>"]
    style m_rust_extensions_src_collections fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_rust_extensions_src["rust_extensions/src/"]
    m_rust_extensions_src -->|1| m_rust_extensions_src_collections
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_rust_extensions_src_collections "/wiki/rust_extensions-src-collections/"
    click m_rust_extensions_src "/wiki/rust_extensions-src/"
{% end %}

## Structure

| Language | Files |
|---|---|
| Rust | 2 |
| Python | 1 |

### Largest Files

- `ring_buffer.rs` (157 lines)
- `mod.rs` (14 lines)
- `__init__.py` (1 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

Used by **1 files** across **1 modules**.

**[rust_extensions/src/](@/wiki/rust_extensions-src.md)** (1 files):
- `lib.rs`



## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>clear</code> (Function) in ring_buffer.rs — referenced in 242 files</p>
<details><summary>Clear all entries from the buffer.</summary>
<div class="doc-comment">
<p>Clear all entries from the buffer.</p>
<p></p>
<p>Does NOT deallocate the backing storage — capacity is preserved.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _base.py, _body_hash.py, _buffer.py +232 more</li></ul>
</li>
<li>
<p><code>capacity</code> (Function) in ring_buffer.rs — referenced in 93 files</p>
<details><summary>Return the maximum capacity of this buffer.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, _buffer.py, _catalog.py, _dedup_stage.py, _hermes_cache.py +83 more</li></ul>
</li>
<li>
<p><code>is_empty</code> (Function) in ring_buffer.rs — referenced in 70 files</p>
<details><summary>Return True if the buffer is empty.</summary></details>
<ul><li class="ref-list">Referenced by: _dedup_stage.py, _enrich_stage.py, _fetch_stage.py, _match_stage.py, _stage_protocol.py +62 more</li></ul>
</li>
<li>
<p><code>push</code> (Function) in ring_buffer.rs — referenced in 51 files</p>
<details><summary>Push an IOC entry onto the ring buffer.</summary>
<div class="doc-comment">
<p>Push an IOC entry onto the ring buffer.</p>
<p></p>
<p>If the buffer is at capacity, the oldest entry is silently evicted.</p>
<p>The entry is stored as a PyObject borrow (no ownership transfer needed</p>
<p>since Python's dict is already reference-counted).</p>
<p></p>
<p>Args:</p>
<p>entry: a Python dict representing the IOC entry</p>
<p></p>
<p>Returns:</p>
<p>The number of entries currently in the buffer after this push</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: aho_corasick.rs, arrow_batch_builder.rs, async_query.rs, bench_gc_314_runtime.py, bloom.rs +45 more</li></ul>
</li>
<li>
<p><code>register_functions</code> (Function) in mod.rs — referenced in 39 files</p>
<details><summary>Register all collections functions with the Python module.</summary></details>
<ul><li class="ref-list">Referenced by: adaptive_scheduler.rs, build_ffi_manifest.py, cache.rs, claims_extraction.rs, compress.rs +32 more</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (11)</summary>
<ul>
<li><code>push</code> (ring_buffer.rs)
<details><summary>Push an IOC entry onto the ring buffer.</summary>
<div class="doc-comment">
<p>Push an IOC entry onto the ring buffer.</p>
<p></p>
<p>If the buffer is at capacity, the oldest entry is silently evicted.</p>
<p>The entry is stored as a PyObject borrow (no ownership transfer needed</p>
<p>since Python's dict is already reference-counted).</p>
<p></p>
<p>Args:</p>
<p>entry: a Python dict representing the IOC entry</p>
<p></p>
<p>Returns:</p>
<p>The number of entries currently in the buffer after this push</p>
</div>
</details>
</li>
<li><code>get_recent</code> (ring_buffer.rs)
<details><summary>Return the most recent N entries (newest first).</summary>
<div class="doc-comment">
<p>Return the most recent N entries (newest first).</p>
<p></p>
<p>Args:</p>
<p>n: number of entries to return (capped at buffer size)</p>
<p></p>
<p>Returns:</p>
<p>List[dict] of the N most recent entries (newest first)</p>
</div>
</details>
</li>
<li><code>get_all</code> (ring_buffer.rs)
<details><summary>Return all entries as a Python list (oldest → newest).</summary>
<div class="doc-comment">
<p>Return all entries as a Python list (oldest → newest).</p>
<p></p>
<p>Returns a NEW list each call — caller owns the list.</p>
<p>Entries are borrowed from the ring (refcount not affected).</p>
<p></p>
<p>Returns:</p>
<p>List[dict] of all entries currently in the buffer</p>
</div>
</details>
</li>
<li><code>new</code> (ring_buffer.rs)</li>
<li><code>register_functions</code> (mod.rs) — <span class="doc-comment-inline">Register all collections functions with the Python module.</span></li>
<li><code>with_capacity</code> (ring_buffer.rs)
<details><summary>Create a new ring buffer.</summary>
<div class="doc-comment">
<p>Create a new ring buffer.</p>
<p></p>
<p>Args:</p>
<p>capacity: maximum number of entries before oldest are evicted (default: 200)</p>
<p></p>
<p>Returns:</p>
<p>A new RingBuffer instance</p>
</div>
</details>
</li>
<li><code>len</code> (ring_buffer.rs) — <span class="doc-comment-inline">Return the current number of entries in the buffer.</span></li>
<li><code>is_empty</code> (ring_buffer.rs) — <span class="doc-comment-inline">Return True if the buffer is empty.</span></li>
<li><code>capacity</code> (ring_buffer.rs) — <span class="doc-comment-inline">Return the maximum capacity of this buffer.</span></li>
<li><code>clear</code> (ring_buffer.rs)
<details><summary>Clear all entries from the buffer.</summary>
<div class="doc-comment">
<p>Clear all entries from the buffer.</p>
<p></p>
<p>Does NOT deallocate the backing storage — capacity is preserved.</p>
</div>
</details>
</li>
<li><code>__repr__</code> (ring_buffer.rs) — <span class="doc-comment-inline">Return a debugging representation.</span></li>
</ul>
</details>

<details><summary><strong>Struct</strong> (1)</summary>
<ul>
<li><code>RingBuffer</code> (ring_buffer.rs)
<details><summary>A bounded ring buffer holding IOC dicts as Python objects.</summary>
<div class="doc-comment">
<p>A bounded ring buffer holding IOC dicts as Python objects.</p>
<p></p>
<p>Backed by a pre-allocated VecDeque of PyObject references.</p>
<p>Push on full: silently evicts oldest entry.</p>
</div>
</details>
</li>
</ul>
</details>

<details><summary><strong>Method</strong> (10)</summary>
<ul>
<li><code>push</code> (ring_buffer.rs)
<details><summary>Push an IOC entry onto the ring buffer.</summary>
<div class="doc-comment">
<p>Push an IOC entry onto the ring buffer.</p>
<p></p>
<p>If the buffer is at capacity, the oldest entry is silently evicted.</p>
<p>The entry is stored as a PyObject borrow (no ownership transfer needed</p>
<p>since Python's dict is already reference-counted).</p>
<p></p>
<p>Args:</p>
<p>entry: a Python dict representing the IOC entry</p>
<p></p>
<p>Returns:</p>
<p>The number of entries currently in the buffer after this push</p>
</div>
</details>
</li>
<li><code>get_recent</code> (ring_buffer.rs)
<details><summary>Return the most recent N entries (newest first).</summary>
<div class="doc-comment">
<p>Return the most recent N entries (newest first).</p>
<p></p>
<p>Args:</p>
<p>n: number of entries to return (capped at buffer size)</p>
<p></p>
<p>Returns:</p>
<p>List[dict] of the N most recent entries (newest first)</p>
</div>
</details>
</li>
<li><code>get_all</code> (ring_buffer.rs)
<details><summary>Return all entries as a Python list (oldest → newest).</summary>
<div class="doc-comment">
<p>Return all entries as a Python list (oldest → newest).</p>
<p></p>
<p>Returns a NEW list each call — caller owns the list.</p>
<p>Entries are borrowed from the ring (refcount not affected).</p>
<p></p>
<p>Returns:</p>
<p>List[dict] of all entries currently in the buffer</p>
</div>
</details>
</li>
<li><code>new</code> (ring_buffer.rs)</li>
<li><code>with_capacity</code> (ring_buffer.rs)
<details><summary>Create a new ring buffer.</summary>
<div class="doc-comment">
<p>Create a new ring buffer.</p>
<p></p>
<p>Args:</p>
<p>capacity: maximum number of entries before oldest are evicted (default: 200)</p>
<p></p>
<p>Returns:</p>
<p>A new RingBuffer instance</p>
</div>
</details>
</li>
<li><code>len</code> (ring_buffer.rs) — <span class="doc-comment-inline">Return the current number of entries in the buffer.</span></li>
<li><code>is_empty</code> (ring_buffer.rs) — <span class="doc-comment-inline">Return True if the buffer is empty.</span></li>
<li><code>capacity</code> (ring_buffer.rs) — <span class="doc-comment-inline">Return the maximum capacity of this buffer.</span></li>
<li><code>clear</code> (ring_buffer.rs)
<details><summary>Clear all entries from the buffer.</summary>
<div class="doc-comment">
<p>Clear all entries from the buffer.</p>
<p></p>
<p>Does NOT deallocate the backing storage — capacity is preserved.</p>
</div>
</details>
</li>
<li><code>__repr__</code> (ring_buffer.rs) — <span class="doc-comment-inline">Return a debugging representation.</span></li>
</ul>
</details>

<details><summary><strong>Constant</strong> (1)</summary>
<ul>
<li><code>RECENT_IOC_RING_CAPACITY</code> (ring_buffer.rs)
<details><summary>Maximum recent IOCs to retain for hypothesis feedback.</summary>
<div class="doc-comment">
<p>Maximum recent IOCs to retain for hypothesis feedback.</p>
<p>Beyond this, oldest entries are silently evicted (ring behavior).</p>
</div>
</details>
</li>
</ul>
</details>

<details><summary><strong>Module</strong> (1)</summary>
<ul>
<li><code>ring_buffer</code> (mod.rs)</li>
</ul>
</details>

<details><summary><strong>Attribute</strong> (3)</summary>
<ul>
<li><code>pyclass</code> (ring_buffer.rs)
<details><summary>A bounded ring buffer holding IOC dicts as Python objects.</summary>
<div class="doc-comment">
<p>A bounded ring buffer holding IOC dicts as Python objects.</p>
<p></p>
<p>Backed by a pre-allocated VecDeque of PyObject references.</p>
<p>Push on full: silently evicts oldest entry.</p>
</div>
</details>
</li>
<li><code>pymethods</code> (ring_buffer.rs)</li>
<li><code>new</code> (ring_buffer.rs)
<details><summary>Create a new ring buffer.</summary>
<div class="doc-comment">
<p>Create a new ring buffer.</p>
<p></p>
<p>Args:</p>
<p>capacity: maximum number of entries before oldest are evicted (default: 200)</p>
<p></p>
<p>Returns:</p>
<p>A new RingBuffer instance</p>
</div>
</details>
</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 172 |
| Avg lines/file | 57 |
| Languages | Python, Rust |
| Outgoing deps | 0 |
| Incoming deps | 1 |
| Tier | 2 |

