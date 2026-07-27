---
confidence: 0.87
sources: [hledac_universal/_index.md, facts/project/_index.md, memory/resource_governor/_index.md]
synthesized_at: '2026-07-18T00:18:19.626Z'
type: synthesis
title: Lazy Loading Reduces Cold Import by 98%
summary: PEP 562 facades and conftest pre-loading cut cold import from ~9.7s to ~150ms for ML inference modules.
tags: [lazy-loading, performance, import, mlx]
related: [architecture/hledac_universal/brain_module_organization.md]
keywords: [pep-562, __getattr__, cold-import, lazy-engine, meta-path, hermes3]
createdAt: '2026-07-18T00:18:19.626Z'
updatedAt: '2026-07-18T00:18:19.626Z'
---

# Lazy Loading Reduces Cold Import by 98%

Brain module uses PEP 562 __getattr__ facade for 12 lazy-loaded engines. Conftest uses _LazyForceLoadFinder prepended to sys.meta_path for 27 hledac.universal subpackages. Combined: ~9.7s → ~150ms cold import. Hermes3Engine is L1 canonical; NEREngine requires large RAM.

## Evidence

- **hledac_universal**: PEP 562 facade reduces cold import from 9.7s to ~150ms, 12 lazy-loaded engines via __getattr__
- **facts/project**: _LazyForceLoadFinder in conftest.py tracks 27 hledac.universal subpackages
- **memory/resource_governor**: Memory optimization P1: per-lane RSS delta telemetry, Rust graph analytics
