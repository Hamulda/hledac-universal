---
children_hash: c486b3d33187b250e982156199021d913fc6ea0e984f3d16f3e99477c85a1999
compression_ratio: 0.24825898738942218
condensation_order: 2
covers: [context.md, core/_index.md, hledac_universal/_index.md, ipc_mechanisms/_index.md, runtime/_index.md, transport_layers/_index.md]
covers_token_total: 5313
summary_level: d2
token_count: 1319
type: summary
---
<think>The user wants me to create a structural summary at level d2 from the provided knowledge entries. Let me analyze the content:

1. **context.md** - Domain overview for architecture
2. **core/_index.md** - Lock registry architecture (deadlock-free, 8-category enum, async lazy init)
3. **hledac_universal/_index.md** - Comprehensive hledac_universal architecture (sprint pipeline, storage trinity, brain module, HTTP/transport, coordinators, sidecar system, critical invariants, performance, Rust extensions, deduplication, layer protocol, telemetry, CLI)
4. **ipc_mechanisms/_index.md** - Zero-copy IPC architecture
5. **runtime/_index.md** - Sprint lifecycle, tiers, canonical boundary, scheduler v2, resource governor, meaningful run detection, report serialization
6. **transport_layers/_index.md** - HTTP/3 lane, Tor/I2P transports, CAPS integration

This is the top-level domain "architecture" summary. I need to create a d2 structural overview that:
- Preserves key facts, relationships, patterns
- References child entry names for drill-down
- Target ~20-30% compression
- Output clean structured markdown
- Do NOT wrap in code blocks or XML tags

Let me create a comprehensive but condensed summary.
</think>



# architecture

Cross-domain knowledge for the hledac Universal OSINT orchestrator—sprint-based acquisition, DuckDB canonical storage, dark web transports, and M1 8GB safety invariants.

## Domain Overview

**Scope**: HTTP/3 lane implementation, proxy routing, Alt-Svc caching, memory guards, zero-copy IPC, sprint lifecycle orchestration, transport layers. **Excludes**: high-level architecture (see hledac_universal), duckdb storage specifics.

**Ownership**: Hledac Universal Team

## Key Architectural Decisions

| Decision | Location | Details |
|----------|----------|---------|
| Deadlock-free locking | `core/lock_registry_architecture.md` | 8-category ascending order (METRICS→CACHE→CONFIG→NETWORK→CURSOR→GRAPH→WAL→MPC) |
| Sprint entry boundary | `runtime/f186a_canonical_sprint_truth.md` | `run_sprint()` sole canonical owner |
| Canonical storage | `hledac_universal/duckdb_kuzu_dual_graph_architecture.md` | DuckDB canonical, LanceDB vectors, LMDB WAL |
| Cold import reduction | `hledac_universal/lazy-loading-reduces-cold-import-by-98.md` | PEP 562 facade: 9.7s → 150ms |
| M1 safety | `hledac_universal/10-critical-invariants-govern-system-stability.md` | 10 GHOST_INVARIANTS enforced via CI |

## Core Architecture (hledac_universal)

**Sprint Pipeline** (12-stage): CLI → `run_sprint()` → `SprintScheduler.run()` → 8 acquisition lanes (surface/structured_ti/deep/archive/CT/WAYBACK/PASSIVE_DNS/PIVOT_EXECUTOR/DOH) → advisory runners → graph accumulation → DuckDB write.

**Storage Trinity**: DuckDB (canonical, 600MB/4 threads), LMDB (WAL, entity hot-edges), LanceDB (ANN vectors, 256d text/1024d image).

**Brain Module**: 12 lazy engines via `__getattr__` (Hermes3 L1, MLX dispatcher, DSPy, NER). BoundedInferencePipeline (ISSUE-17) with 3-stage queue <200KB.

**HTTP/Transport**: curl_cffi + aioquic dual strategy, BLAKE3-64 body hashing (5GB/s), Tor/I2P darknet support.

**5 Coordinators**: Fetch (AIMD, 25 window), Resource (BlitzGCStrategy), Memory (L1/L2 + FAISS/HNSW), Sidecar (17 adapters), Execution.

**Sidecar System** (F205B): 17 adapters with env gates, 3-stage execution (light→correlation→derived), 5-branch parallel teardown (ISSUE-3: 30-90s → 5-15s).

## Runtime Domain (runtime)

**Sprint Tiers**: quick (60-179s), standard (180-299s), deep (300-599s), thorough (600s+). MIN_ACTIVE_WINDOW_S = 30s.

**Scheduler V2**: `SprintSchedulerV2` production, v1 archived. PivotTask relocated to `runtime/pivot_types.py`.

**Resource Governor**: `M1ResourceGovernor` with 5-tier UMA state, EMA adaptive concurrency (alpha=0.3), sidecar admission blocks.

**Meaningful Run Detection**: Hardware-limited smoke / smoke / meaningful (pattern hits ≥15) / not meaningful.

**Report Serialization**: orjson with OPT_INDENT_2, numpy auto-detect, `HLEDAC_REPORT_PRETTY_PRINT=1`.

**Sprint Seed State**: Global `_current_sprint_seed_state` for deterministic cognitive replay.

## Transport Layers

**HTTP/3 Lane**: 3 strategies (curl_cffi_opportunistic → NeqoRustls → Aioquic). LRU 512 entries, 3 concurrent, 5.5GiB RSS block, 24h TTL.

**Tor**: socks5h://127.0.0.1:9050, circuit rotation every 10 requests, 2.0x timeout, JARM C2 fingerprinting (Cobalt Strike/Metasploit/AsyncRAT/Havoc/Covenant).

**I2P**: SAM v3 (7656, ~2MB RAM), SOCKS5H (4444), HTTP proxy (8888). Session ID: hledac-samv3-<uuid>.

**CAPS Integration**: FetchCoordinator requires curl_cffi CAPS for JA3 spoofing—fail-fast on unavailable.

## IPC Mechanisms

Zero-copy architecture: SharedMemory (M1), Arrow IPC, DuckDB shared cache, msgspec serialization, LMDB WAL persistence, EventBus for sidecar communication.

## Cross-Domain Contracts

- `run_sprint()` owns sprint_delta reporting → `duckdb_store/context.md` for canonical write
- 10 critical invariants span runtime + hledac_universal
- Sidecar 17 adapters wired via `sidecar_bus_architecture.md` → `sidecar_protocol_registry.md`
- Sprint flags (no_communication, no_stealth, no_ghost, no_coordination) control layer injection
- Resource governor telemetry flows through acquisition_orchestrator_lifecycle.md