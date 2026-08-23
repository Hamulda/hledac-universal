# Project Map Index

## Entry Count

**18 features · 4 flows · 5 surfaces · 12 components · 73 modules · 15 utilities · 12 patterns · 5 contracts · 7 domains = 151 entries**

Last full catalog: 2026-08-20 (deep expansion)

## Features

| Entry Path | Status | Evidence Level |
| --- | --- | --- |
| features/sprint-pipeline.md | current | source |
| features/feed-pipeline.md | current | source |
| features/mlx-inference.md | current | source |
| features/stealth-fetch.md | current | source |
| features/ioc-extraction.md | current | source |
| features/semantic-dedup.md | current | source |
| features/rag-search.md | current | source |
| features/tree-of-thoughts.md | current | source |
| features/telemetry-export.md | current | source |
| features/pivot-orchestration.md | current | source |
| features/multimodal-processing.md | current | source |
| features/dlp-filtering.md | current | source |
| features/osint-lane-orchestration.md | **NEW** | source |
| features/certificate-transparency.md | **NEW** | source |
| features/blockchain-osint.md | **NEW** | source |
| features/network-intelligence.md | **NEW** | source |
| features/multimodal-evidence.md | **NEW** | source |
| features/rust-accelerated-ioc.md | **NEW** | source |

## Flows

| Entry Path | Status | Evidence Level |
| --- | --- | --- |
| flows/sprint-lifecycle.md | current | source |
| flows/fetch-pipeline.md | current | source |
| flows/pivot-lane-planning.md | current | source |
| flows/evidence-lifecycle.md | current | source |

## Surfaces

| Entry Path | Status | Evidence Level |
| --- | --- | --- |
| surfaces/cli.md | current | source |
| surfaces/mlx-embeddings-api.md | current | source |
| surfaces/duckdb-api.md | current | source |
| surfaces/rust-backend.md | current | source |
| surfaces/tor-anonymity-surface.md | **NEW** | source |

## Components

| Entry Path | Status | Evidence Level |
| --- | --- | --- |
| components/coordinator-base.md | current | source |
| components/duckdb-shadow-store.md | current | source |
| components/stage-protocol.md | current | source |
| components/memory-layer.md | current | source |
| components/pattern-matcher.md | current | source |
| components/layer-registry.md | current | source |
| components/rust-pipeline-composer.md | current | source |
| components/tool-sprint-gate.md | **NEW** | source |
| components/tool-vlm-analyzer.md | **NEW** | source |
| components/tool-whisper-transcriber.md | **NEW** | source |
| components/tool-ocr-engine.md | **NEW** | source |
| components/tool-content-extractor.md | **NEW** | source |

## Modules

### Core Infrastructure
| Entry Path | Status |
| --- | --- |
| modules/core-embeddings-pool.md | **NEW** |
| modules/core-embeddings-manager.md | **NEW** |
| modules/core-resource-governor.md | **NEW** |
| modules/core-lmdb-unified.md | current |
| modules/core-lock-registry.md | current |

### Rust FFI Wiring (NEW SECTION)
| Entry Path | Status |
| --- | --- |
| modules/rust-wiring/rust-ioc-dedup-wiring.md | **NEW** |
| modules/rust-wiring/rust-bloom-filter-wiring.md | **NEW** |
| modules/rust-wiring/rust-simd-similarity-wiring.md | **NEW** |
| modules/rust-wiring/rust-circuit-breaker-wiring.md | **NEW** |
| modules/rust-wiring/rust-graph-analytics-wiring.md | **NEW** |
| modules/rust-wiring/rust-aimd-wiring.md | **NEW** |
| modules/rust-wiring/rust-claims-extraction-wiring.md | **NEW** |
| modules/rust-wiring/rust-text-norm-wiring.md | **NEW** |
| modules/rust-wiring/rust-url-engine-wiring.md | **NEW** |

### Transport Layer (NEW)
| Entry Path | Status |
| --- | --- |
| modules/transport-tor.md | **NEW** |
| modules/transport-i2p.md | **NEW** |
| modules/transport-arti.md | **NEW** |
| modules/transport-nym.md | **NEW** |
| modules/transport-session-pool.md | **NEW** |
| modules/transport-circuit-breaker.md | **NEW** |

### Security Layer (NEW)
| Entry Path | Status |
| --- | --- |
| modules/security-stealth-engine.md | **NEW** |
| modules/security-captcha-solver.md | **NEW** |
| modules/security-quantum-crypto.md | **NEW** |
| modules/security-pii-gate.md | **NEW** |
| modules/security-vault-manager.md | **NEW** |
| modules/security-ephemeral-wipe.md | **NEW** |

### Recon Lanes (NEW)
| Entry Path | Status |
| --- | --- |
| modules/recon-shodan-lane.md | **NEW** |
| modules/recon-greynoise-lane.md | **NEW** |
| modules/recon-dark-web-lane.md | **NEW** |
| modules/recon-ct-log-scanner.md | **NEW** |
| modules/recon-wayback-cdx.md | **NEW** |
| modules/recon-github-secret-scanner.md | **NEW** |

### Network Intelligence (NEW)
| Entry Path | Status |
| --- | --- |
| modules/network-passive-dns.md | **NEW** |
| modules/network-bgp-monitor.md | **NEW** |
| modules/network-dns-tunnel-detector.md | **NEW** |

### Multimodal (NEW)
| Entry Path | Status |
| --- | --- |
| modules/multimodal-media-engine.md | **NEW** |
| modules/multimodal-vision-encoder.md | **NEW** |
| modules/multimodal-evidence-triage.md | **NEW** |

### Previously Cataloged
| Entry Path | Status |
| --- | --- |
| modules/cli-parser.md | current |
| modules/composition-root.md | current |
| modules/capabilities-core.md | current |
| modules/capabilities-registry.md | current |
| modules/fetch-coordinator.md | current |
| modules/pipeline-orchestrator.md | current |
| modules/execution-coordinator.md | current |
| modules/memory-coordinator.md | current |
| modules/memory-manager.md | current |
| modules/graph-manager.md | current |
| modules/hypothesis-graph.md | current |
| modules/ioc-processor.md | current |
| modules/curl-cffi-fetch.md | current |
| modules/circuit-breaker.md | current |
| modules/monitoring-coordinator.md | current |
| modules/duckdb-pool.md | current |
| modules/otel.md | current |
| modules/bounded-collections.md | current |
| modules/research-optimizer.md | current |
| modules/meta-reasoning-coordinator.md | current |
| modules/opsec-coordinator.md | current |
| modules/security-coordinator.md | current |
| modules/multimodal-coordinator.md | current |
| modules/performance-coordinator.md | current |
| modules/hermes-model-cache.md | current |
| modules/mlx-kv-cache-share.md | current |
| modules/duckdb-shadow-store.md | current |
| modules/context-compressor.md | current |
| modules/ioc-pattern-matcher.md | current |
| modules/duckdb-vector-store.md | current |
| modules/session-pool.md | current |
| modules/stealth-browser.md | current |
| modules/input-detector.md | current |
| modules/bloom-filter.md | current |
| modules/graph-facade.md | current |

## Utilities

| Entry Path | Status | Evidence Level |
| --- | --- | --- |
| utilities/paths.md | current | source |
| utilities/resource-allocator.md | current | source |
| utilities/evidence-writer.md | current | source |
| utilities/env-config.md | current | source |
| utilities/async-helpers.md | current | source |
| utilities/optional-imports.md | current | source |
| utilities/bounded-collections.md | current | source |
| utilities/async-cache.md | **NEW** | source |
| utilities/adaptive-cache.md | **NEW** | source |
| utilities/asyncx-core.md | **NEW** | source |
| utilities/thread-pool-utils.md | **NEW** | source |
| utilities/rayon-pool-utils.md | **NEW** | source |
| utilities/intelligent-cache.md | **NEW** | source |
| utilities/memory-tier-utils.md | **NEW** | source |
| utilities/mlx-prompt-cache.md | **NEW** | source |

## Patterns

| Entry Path | Status | Evidence Level |
| --- | --- | --- |
| patterns/fail-loud-circuit-breaker.md | current | source |
| patterns/bounded-queue-stage-chain.md | current | source |
| patterns/task-local-context.md | current | source |
| patterns/aimd-parallel.md | current | source |
| patterns/deprecation-wrapper.md | current | source |
| patterns/stage-level-execution.md | current | source |
| patterns/lazy-composition.md | current | source |
| patterns/arrow-zero-copy.md | current | source |
| patterns/embedding-pool.md | **NEW** | source |
| patterns/resource-governance.md | **NEW** | source |
| patterns/adaptive-cache.md | **NEW** | source |
| patterns/async-pool-pattern.md | **NEW** | source |

## Contracts

| Entry Path | Status | Evidence Level |
| --- | --- | --- |
| contracts/duckdb-write-contract.md | current | source |
| contracts/coordinator-interface.md | current | source |
| contracts/fetch-fallback-chain.md | current | source |
| contracts/lmdb-write-contract.md | current | source |
| contracts/rust-ffi-contract.md | **NEW** | source |

## Domains

| Entry Path | Status | Evidence Level |
| --- | --- | --- |
| domains/osint-orchestration.md | current | source |
| domains/m1-memory-management.md | current | source |
| domains/recon-lanes.md | current | source |
| domains/graph-analytics.md | current | source |
| domains/stealth-networking.md | current | updated |
| domains/evidence-lifecycle.md | current | source |
| domains/crypto-operations.md | **NEW** | source |
| domains/osint-recon-lanes.md | **NEW** | source |

## Deprecated

| Entry Path | Status | Note |
| --- | --- | --- |
| modules/ioc-extractor.md | deprecated | Use knowledge.ioc_processor |
| modules/layer-manager.md | deprecated | Use layers.core.LayerRegistry |

## Section Indexes

- [features/](features/index.md)
- [flows/](flows/index.md)
- [surfaces/](surfaces/index.md)
- [components/](components/index.md)
- [modules/](modules/index.md)
- [modules/rust-wiring/](modules/rust-wiring/index.md)
- [utilities/](utilities/index.md)
- [patterns/](patterns/index.md)
- [contracts/](contracts/index.md)
- [domains/](domains/index.md)
- [glossary/](glossary/index.md)

---

## Deep Expansion Summary (2026-08-20)

Added **69 new entries** across 9 new areas:

### New Sections
- **Rust FFI Wiring** (9 entries): IOC dedup, Bloom filter, SIMD similarity, circuit breaker, graph analytics, AIMD, claims extraction, text norm, URL engine
- **Transport Layer** (6 entries): Tor, I2P, Arti, Nym, session pool, circuit breaker
- **Security Layer** (6 entries): Stealth engine, captcha solver, quantum crypto, PII gate, vault manager, ephemeral wipe
- **Recon Lanes** (6 entries): Shodan, GreyNoise, dark web, CT logs, Wayback, GitHub secrets
- **Network Intelligence** (3 entries): Passive DNS, BGP monitor, DNS tunnel detector
- **Multimodal** (3 entries): Media engine, vision encoder, evidence triage
- **Core Infrastructure** (3 entries): Embeddings pool/manager, resource governor
- **Tools** (4 entries): Sprint gate, VLM analyzer, whisper, OCR
- **Utilities** (6 entries): Async/adaptive cache, asyncx, thread pools
- **Features** (5 entries): OSINT lanes, CT, blockchain, network intel, multimodal evidence

### Key Architectural Insights

1. **Dual-engine pattern**: Rust FFI + Python for high-throughput paths
2. **Anonymity trinity**: Tor + I2P + Nym for layered anonymity
3. **Lane-based recon**: AcquisitionLane pattern for extensible OSINT sources
4. **Multimodal pipeline**: Media → Vision/Audio → IOC extraction
5. **M1 budget enforcement**: ResourceGovernor + Metal cache limits
