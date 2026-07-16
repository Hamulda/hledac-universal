---
title: Sidecar Protocol Registry
summary: Sidecar Protocol Registry with 17 adapters, lazy imports, memory admission, and finding output format
tags: []
related: []
keywords: []
createdAt: '2026-07-16T11:05:00.672Z'
updatedAt: '2026-07-16T11:05:00.672Z'
---
## Reason
Document F350M-R sidecar architecture with 17 adapters and registry mechanics

## Raw Concept
**Task:**
Document Sidecar Protocol Registry architecture (F350M-R)

**Changes:**
- Added 17 sidecar adapters with registry mechanics
- Implemented lazy imports for expensive dependencies
- Added memory admission via GovernorDecision.sidecar_admission
- Added prewarm API for parallel initialization

**Files:**
- runtime/sidecar_protocol.py
- runtime/sidecar_protocol_adapters.py

**Flow:**
sprint start -> Registry.prewarm_async() -> memory check -> run adapters by priority -> collect findings

**Timestamp:** 2025-01-15

**Patterns:**
- `^HLEDAC_ENABLE_\w+$` - Env var pattern for enabling sidecar adapters
- `sidecar_id|env_gate|ram_budget_mb` - Required adapter protocol fields

## Narrative
### Structure
SidecarProtocol defines run(ctx) and is_available(). BaseSidecarAdapter provides shared init/shutdown. Registry handles auto-registration, lazy import, memory admission, and discovery.

### Dependencies
Requires runtime_checkable Protocol, msgspec.Struct for SidecarContext, GovernorDecision for memory admission

### Highlights
17 adapters: fediverse(50MB/6), dht(100MB/4), academic(80MB/5), alt_protocols(60MB/4), leak_sentinel(30MB/3), tvnews(40MB/5), federated_research(30MB/5), passive_fingerprint(50MB/4), passive_tech_stack(30MB/4), social_identity_surface(60MB/5), identity_stitching(100MB/5), temporal_archaeology(80MB/4), lancedb_rag(60MB/7), github_gist(30MB/5), whois(30MB/5), threat_intel(40MB/7), shadow_walker(80MB/5)

### Rules
Rule 1: ram_budget_mb checked before every run
Rule 2: GHOST_INVARIANTS: all methods fail-safe wrapped
Rule 3: No blocking ops in async context
Rule 4: Auto-registration via @SidecarRegistry.register decorator

### Examples
Example finding output: {"source_type": "fediverse", "ioc_type": "domain", "ioc_value": "example.com", "confidence": 0.85}

## Facts
- **sidecar_files**: Sidecar protocol files are runtime/sidecar_protocol.py and runtime/sidecar_protocol_adapters.py [project]
- **adapter_count**: 17 sidecar adapters are registered in the registry [project]
- **academic_aggressive_skip**: Academic adapter skips in aggressive mode to save ~50s [project]
- **lazy_imports**: Lazy imports used for expensive modules (GLiNER, cryptography) to avoid 200+ms boot cost [project]
- **instance_caching**: Instance caching enabled via _cached_instances after first successful instantiation [project]
- **high_priority_adapters**: Priority 7 adapters (lancedb_rag, threat_intel) run early in the pipeline [project]
