---
title: Phase 2 Architecture and Directory Structure
summary: 'Phase 2 exploration: directory layout across core/runtime/knowledge/fetching/transport/brain modules, CLI entry points, and 8-lane sprint data flow'
tags: []
related: []
keywords: []
createdAt: '2026-07-27T13:08:46.452Z'
updatedAt: '2026-07-27T13:08:46.452Z'
---
## Reason
Document Phase 2 architecture exploration findings

## Raw Concept
**Task:**
Document Phase 2 architecture exploration of hledac_universal

**Flow:**
CLI → run_sprint() → SprintScheduler.run() → 8 acquisition lanes → advisory runners → graph accumulation → DuckDB canonical write

**Timestamp:** 2026-07-27

## Narrative
### Structure
Directory layout: core/ (resource_governor, locks, capabilities, optional_imports), runtime/ (sprint_scheduler, sprint_entrypoint), knowledge/ (duckdb_store, graph_service/DuckPGQGraph, lancedb_store), fetching/ (public_fetcher/curl_cffi, fetch_coordinator), transport/ (http3_lane, prewarm_pool, conditional_cache, tor/i2p/nym transports), brain/ (inference_engine, dspy_optimizer, hypothesis_engine, ner_engine, mlx_batched_executor), coordinators/ (fetch_coordinator, sidecar_orchestrator), sidecar/ (protocol + adapters: fediverse, dht, academic, alt_protocols, leak_sentinel), tests/ (test_sprint_*, test_exit_codes, probe_*/test_*.py), rust_extensions/ (PyO3: feed_pipeline, ioc_extractor, url_ops, content_hasher, batch_counters)

### Dependencies
Entry: python -m hledac.universal --sprint "QUERY" [--duration SECS] [--aggressive]

### Highlights
SprintScheduler.run() orchestrates 8 acquisition lanes. DuckDB is canonical write target. Rust extensions via PyO3 bridge.

### Examples
runtime/sprint_entrypoint.py is current entry (deprecated: core/__main__.py)
