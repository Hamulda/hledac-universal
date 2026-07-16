---
title: Rust Extensions Overview
summary: 30+ Rust modules (MPSC, bloom filters, hashing, IOC extraction), crossbeam-channel MPSC with ARM LSE atomics, feature-gated compilation, memory budgets
tags: []
related: [facts/project/xxhash_rust_implementation.md, facts/project/technology_stack.md, facts/project/parallel_async_helper.md]
keywords: []
createdAt: '2026-07-16T11:01:16.887Z'
updatedAt: '2026-07-16T11:01:16.887Z'
---
## Reason
Document 30+ Rust source files in rust_extensions/src/ with MPSC pool, feature flags, and crossbeam integration

## Raw Concept
**Task:**
Document Rust extensions architecture and 30+ source modules

**Files:**
- rust_extensions/src/mpsc_pool.rs
- rust_extensions/src/adaptive_scheduler.rs
- rust_extensions/src/async_query.rs
- rust_extensions/src/arrow_batch_builder.rs
- rust_extensions/src/bloom.rs
- rust_extensions/src/dedup_bloom.rs
- rust_extensions/src/claims_extraction.rs
- rust_extensions/src/compress.rs
- rust_extensions/src/content_hasher.rs
- rust_extensions/src/crypto_accelerate.rs
- rust_extensions/src/graph_cache.rs
- rust_extensions/src/url_ops.rs
- rust_extensions/Cargo.toml

**Flow:**
Python async thread -> crossbeam MPSC bounded channel -> pipe wake-up fd -> asyncio.Event -> recv_batch drain

**Timestamp:** 2026-07-16

## Narrative
### Structure
30+ Rust source files in rust_extensions/src/ organized by function: async execution (adaptive_scheduler, async_query), data structures (bloom, dedup_bloom, graph_cache), processing (claims_extraction, content_hasher, url_ops, compress, crypto_accelerate), I/O (arrow_batch_builder), concurrency (mpsc_pool)

### Dependencies
Requires pyo3 0.23 with extension-module and abi3-py39 features; crossbeam-channel 0.5; duckdb 1; xxhash-rust 0.8 with xxh3/xxh64 features; metal 0.33 for macOS

### Highlights
MPSC pool replaces asyncio.Queue(maxsize=500) with crossbeam bounded channel; ARM LSE atomic instructions (ldadd, cas) on aarch64 for ~2-5ns per send; zero-copy bytes via msgspec.msgpack.encode(); #[pyclass(unsendable)] for non-Send types; memory budget: 2048 slots × 512B ≈ 1 MiB total

### Rules
MPSCPool MUST have #[pyclass(unsendable)] because Receiver<QueueItem> is not Send; add_sender() pushes cloned sender, not original; bounded(N) pre-allocates ring buffer never grows

## Facts
- **rust_source_count**: Rust extensions are in rust_extensions/src/ with 30+ source files [project]
- **mpsc_memory_budget**: MPSC pool uses 2048 slots × 512B = ~1 MiB memory budget [project]
- **mpsc_replaces_asyncio_queue**: MPSC pool replaces asyncio.Queue(maxsize=500) from evidence_log.py [project]
- **feature_flags**: Feature flags: default=core, ml=macOS only, graph, data, advanced, full=CICD [project]
- **crossbeam_performance**: crossbeam-channel on aarch64 uses ARM LSE atomic instructions for ~2-5ns per send [project]
