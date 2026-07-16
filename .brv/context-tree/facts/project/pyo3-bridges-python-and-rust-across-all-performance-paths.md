---
confidence: 0.88
sources: [facts/project/_index.md, memory/resource_governor/_index.md, facts/project/_index.md, memory/resource_governor/_index.md]
synthesized_at: '2026-07-16T11:30:38.724Z'
type: synthesis
title: PyO3 Bridges Python and Rust Across All Performance Paths
summary: Hashing facade, MPSC batch sends, and crossbeam interop all use PyO3 — single FFI strategy for critical paths.
tags: [rust, pyo3, interop, ffi, concurrency]
related: []
keywords: [pyo3, rust, python, maturin, send_batch, mpsc, hashing, ffi, crossbeam]
createdAt: '2026-07-16T11:30:38.724Z'
updatedAt: '2026-07-16T11:30:38.724Z'
---

# PyO3 Bridges Python and Rust Across All Performance Paths

PyO3 is the standard Python↔Rust boundary, not just for one subsystem but for hashing, concurrency, and storage — consistent IPC model.

## Evidence

- **facts/project**: Centralized hashing facade at utils/hashing.py uses Rust xxhash-rust
- **memory/resource_governor**: Rust send_batch enables single Python→Rust call for N items via PyO3 MPSC pool
- **facts/project**: Rust extensions built via PyO3/Maturin for async execution
- **memory/resource_governor**: Receiver<QueueItem> NOT Send — PyO3 object binding constraint
