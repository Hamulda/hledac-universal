---
title: HTTP/3 neqo Integration Plan
summary: 'neqo integration plan: add neqo-http3 crate, expose neqo_fetch via PyO3, M1 arm64 darwin priority'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:19:10.468Z'
updatedAt: '2026-07-26T11:19:10.468Z'
---
## Reason
Documenting F320-TODO neqo integration plan

## Raw Concept
**Task:**
Document neqo HTTP/3 integration plan (F320-TODO)

**Changes:**
- neqo not on PyPI yet - pending
- Stub returns False and falls back to aioquic

**Flow:**
neqo-http3 crate -> PyO3 exposure -> neqo_fetch import

**Timestamp:** 2026-07-26

## Narrative
### Structure
Integration plan for Mozilla neqo Rust QUIC engine with rustls TLS

### Highlights
Loaded only on arm64+darwin where rustls memory arenas release immediately. rustls arena release automatic when Rust Connection drops.

### Examples
Step 1: Add neqo-http3 crate to Cargo.toml [dependencies]
Step 2: Expose async fn neqo_fetch(url: &str, ...) -> PyResult<Vec<u8>> via PyO3
Step 3: Import via from hledac.universal.rust_extensions import neqo_fetch
