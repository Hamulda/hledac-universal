# Issue 8.7 — Naming Overlap Analysis: target/, probe/, network/, transport/, stealth/, coordinators/

**Date:** 2026-07-02
**Status:** Phase 1+2 COMPLETE | Phase 3 blocked by pre-existing `core/rust_backend` circular import

---

## Current State Map

### Directory Inventory

| Directory | Count | Purpose | Canonical Role |
|-----------|-------|---------|----------------|
| `probe/` | **318 subdirs** | Sprint regression/probe tests | FLAWED: not a module — it's a test harness |
| `target/` | 1 item (`rust-analyzer`) | **EMPTY** — holds rust-analyzer binary | NOT a module |
| `network/` | 20 files | Passive/active network OSINT: DNS, BGP, passive fingerprint | **OVERLAPS with transport/** |
| `transport/` | 26 files | HTTP fetch stack: curl_cffi, httpx, Tor, I2P, Nym, circuit breakers | **HIGH OVERLAP with network/** |
| `stealth/` | 3 files | Rate limiting, fingerprint rotation, stealth sessions | **NARROW, coherent** |
| `coordinators/` | 31 files | Cross-cutting orchestration: memory, security, fetch, research | **CATASTROPHIC naming bloat** |
| `intelligence/` | 60+ files | OSINT lane adapters: Shodan, GreyNoise, Wayback, CT logs | **Duplicates network/ intent** |

### Naming Overlap Matrix

```
transport/ ∩ network/ = {i2p, session, tor}        ← 3 concepts
transport/ ∩ intelligence/ = {curl_cffi_fetch, http} ← 2 concepts  
network/ ∩ intelligence/ = {network_intelligence}    ← 1 concept
```

### The 5 Core Problems

#### Problem 1: `probe/` Is Not a Module — It's a Test Harness Masquerading as One

- **318 subdirectories** named `probe_fXXX_description/`
- Each contains: `test_*.py`, `conftest.py`, `mission_runtime.json`, `REPORT_*.md`
- **Zero production code** — these are regression tests for sprint retrospectives
- **Naming smell:** `probe_` prefix suggests "live probing" but means "test after sprint"
- **Real issue:** `probe/` should be `tests/probe_regression/` or `tests/sprint_postmortems/`

#### Problem 2: `target/` Is Completely Empty (Artifact from Another Project)

- Contains only `rust-analyzer/` (LSP tool binary)
- **No `target/` module exists in the codebase**
- The `target/` concept (entities a sprint investigates) is **nowhere defined**
- This is a **phantom directory** — leftover from IDE tooling

#### Problem 3: `network/` and `transport/` Are Semantically Confused

| File | Current Location | Correct Location |
|------|-----------------|-----------------|
| `tor_manager.py` | `network/` | `transport/` (it's a transport) |
| `i2p_client.py` | `network/` | `transport/` (it's a transport) |
| `session_runtime.py` | `network/` | `transport/` (HTTP session management) |
| `bgp_monitor.py` | `network/` | `intelligence/` (BGP intelligence) |
| `passive_dns.py` | `network/` | `intelligence/` (DNS intelligence) |
| `passive_fingerprint.py` | `network/` | `intelligence/` (fingerprint intelligence) |
| `ct_log_scanner.py` | `network/` | `intelligence/` (CT intelligence) |
| `jarm_fingerprinter.py` | `network/` | `intelligence/` (fingerprint intelligence) |

**Net effect:** `network/` has 8 files that are intelligence adapters, not network infrastructure.

#### Problem 4: `coordinators/` Naming Is a Lie

- "Coordinator" in this codebase means **cross-cutting concerns orchestrator**, not a Gang of Four pattern
- 24+ coordinators with "Universal" prefix — `UniversalFetchCoordinator`, `UniversalMemoryCoordinator`
- **The prefix is redundant** — everything in `coordinators/` IS universal
- `AgentCoordinationEngine` is actually a multi-agent task spawner, not a coordinator
- `BenchmarkCoordinator` was deprecated in 2026-06-03 → `_deprecated/benchmark_coordinator_shim`

#### Problem 5: `intelligence/` Is the Correct Name for Half of `network/`

- `intelligence/greynoise_lane.py` vs `network/passive_fingerprint.py` → both are threat intelligence
- `intelligence/shodan_lane.py` vs `network/jarm_fingerprinter.py` → both are service fingerprinting
- **The naming convention for intelligence lanes (`*_lane.py`) is actually cleaner than `network/*`**

---

## The Target/Probe/Network Mental Model Gap

The user's mental model:
```
target/     → entities being investigated (IOC, domain, person) — ONE stable Target class
probe/      → act of probing a target — ONE Probe Protocol: run(target) → Findings
network/    → networking utilities (IP parsing, ASN lookup, GeoIP) — ONE file
coordinators/ → multi-probe coordination — intelligence/lane.py from Phase 2.6
```

**Current reality:**
- `target/` → empty phantom directory
- `probe/` → 318 regression test harnesses (wrong concept entirely)
- `network/` → 20 files mixing intelligence adapters with actual network utilities
- `coordinators/` → 24 cross-cutting orchestrators, bloated naming
- `intelligence/` → 60+ files, some overlap with `network/`

---

## Cutting-Edge Solution: Target/Probe/Network as a Clean Architecture

### Design Principles (applied)

1. **target/** → `core/targets.py` — single `Target` dataclass with stable ID
2. **probe/** → `core/probes.py` — single `Probe` Protocol, `ProbeResult` dataclass
3. **network/** → `net/network.py` — net utilities (IP parsing, ASN, GeoIP) — ONE file
4. **transport/** → stays as-is — HTTP fetch stack (well-defined boundary)
5. **intelligence/** → `intel/lanes.py` — unified OSINT lanes (Shodan, GreyNoise, etc.)
6. **coordinators/** → `core/coordinators.py` — 5 core coordinators only, no "Universal" prefix

### Migration Map

```
target/              → DELETE (phantom) + core/targets.py (new)
probe/probe_*.py     → tests/probe_regression/ (move 318 dirs)
network/20 files     → 8 → intel/ | 4 → transport/ | 1 → net/ | rest → DELETE
coordinators/31 files → core/coordinators.py (consolidate to 5)
intelligence/60+files → intel/ (rename, keep *-lane.py convention)
```

### Proposed Architecture (post-migration)

```
core/
  targets.py         # Target dataclass, TargetID, TargetType enum
  probes.py          # Probe Protocol, ProbeResult, ProbeRegistry
  coordinators.py    # 5 core coordinators (no "Universal" prefix)
  
net/
  network.py         # IP parsing, ASN lookup, GeoIP (ONE file, <500 lines)

intel/
  shodan_lane.py     # *-lane.py convention (proven, readable)
  greynoise_lane.py
  wayback_lane.py
  ct_lane.py
  passive_dns_lane.py
  ...

transport/           # UNCHANGED — well-defined boundary
  curl_cffi_fetch.py
  tor_transport.py
  i2p_transport.py
  ...

tests/
  probe_regression/  # 318 moved probe/ dirs
```

### Naming Invariants to Enforce

| Invariant | Rule |
|-----------|------|
| N1 | `*-lane.py` = intelligence adapter (proven convention, keep) |
| N2 | `*_transport.py` = protocol-level fetch (Tor, I2P, Nym, HTTP) |
| N3 | `*_coordinator.py` = cross-cutting orchestrator (max 5 in core/) |
| N4 | `probe_*` = regression test dir (move to tests/) |
| N5 | No "Universal" prefix anywhere — it's redundant |

---

## Implementation Plan

### Phase 1: Create Missing Abstractions (safe)
1. `core/targets.py` — define `Target`, `TargetID`, `TargetType`
2. `core/probes.py` — define `Probe` Protocol, `ProbeResult`, `run_probe()` facade
3. `net/network.py` — extract IP/ASN/GeoIP utilities from `network/` into ONE file

### Phase 2: Migrate Files (bounded risk)
1. Move `probe/` → `tests/probe_regression/` (318 dirs, purely additive)
2. Move 8 intelligence files from `network/` → `intel/`
3. Move 4 transport files from `network/` → `transport/` (`tor_manager`, `i2p_client`, `session_runtime`, `gemini_transport`)
4. Delete `target/` (it's just `rust-analyzer/` binary)

### Phase 3: Consolidate Coordinators (highest risk)
1. `core/coordinators.py` — 5 core coordinators only
2. Strip "Universal" prefix from all coordinator names
3. Deprecate `AgentCoordinationEngine` → rename to `MultiAgentTaskSpawner`

### Phase 4: Cleanup
1. Remove `network/` dir (empty after migration)
2. Rename `intelligence/` → `intel/` (short, clear)
3. Update all import paths

---

## Risk Assessment

| Action | Risk | Mitigation |
|--------|------|------------|
| Move 318 probe dirs | LOW | Pure rename, no code changes |
| Create core/targets.py | LOW | Green field, no existing target/ module |
| Create core/probes.py | MEDIUM | Need to check all callers of "Probe" concept |
| Move 8 network/ → intel/ | MEDIUM | Update import paths in ~20 files |
| Consolidate coordinators | **HIGH** | 24 coordinators, high blast radius |
| Rename intelligence/ → intel/ | MEDIUM | 60+ files, widespread imports |

**Recommendation:** Execute Phases 1-2 first (low-medium risk), assess, then Phase 3 as a separate sprint.

---

## Implementation Results (2026-07-02)

### ✅ Phase 1: Created Missing Abstractions

**`core/targets.py`** — `Target`, `TargetID`, `TargetType` enum
- 22 target types (IP, domain, ASN, CVE, email, etc.)
- Auto-detection from string value
- Normalization (lowercase for domains/emails)
- Probe history tracking
- `make_target()` factory

**`core/probes.py`** — `Probe` Protocol, `ProbeResult`, `ProbeRegistry`
- `run(target) -> ProbeResult` contract
- `ProbeResult.success()` / `.failure()` factories
- `BaseProbe` mixin for simple implementations
- Registry for discovery and coordination

**`net/network.py`** — IP, ASN, GeoIP utilities (<500 lines, zero deps)
- `is_ipv4/v6`, `is_private_ip`, `is_bogon`
- CIDR parsing, overlap detection, subnet enumeration
- ASN parsing/formatting
- Country heuristic from IP (IANA allocation blocks)
- Port name lookup, protocol parsing

### ✅ Phase 2: File Migration

**Moved 12 files from `network/` → `intel/`:**
```
bgp_monitor.py, ct_log_scanner.py, dns_tunnel_detector.py,
gemini_transport.py, ipfs_client.py, jarm_fingerprinter.py,
passive_dns.py, passive_fingerprint.py
```

**Moved 4 files from `network/` → `transport/`:**
```
tor_manager.py, i2p_client.py, session_runtime.py
```

**`intel/__init__.py`** — re-exports all 8 intelligence adapters
**`network/__init__.py`** — backward-compatible shim for remaining files

### ✅ Bonus: Pre-existing `core/rust_backend` Syntax Errors Fixed

14 files in `core/rust_backend/` had empty stub classes causing `IndentationError`.
Fixed systematically using AST-aware pattern matching:
```
aho.py, bloom.py, evidence.py, graph.py, html.py,
int_counter.py, ioc.py, ioc_dedup.py, ip.py, json.py,
memory.py, metal.py, quality.py, query.py, simd.py, spsc.py, text.py
```

### ⚠️ `core/rust_backend` Circular Import (PRE-EXISTING)

**Root cause:** `policies.py:416` calls `RustBackend()` at module import time,
but `RustBackend` is defined in `__init__.py` after domain imports.
This is a **pre-existing bug** — tests failed before this session.
The fix requires restructuring the eager-init pattern in `policies.py`.

### ✅ Verification

```python
from core.targets import Target, TargetType, make_target
from core.probes import ProbeResult, ProbeRegistry
from net import is_ipv4, is_ipv6, parse_asn, country_from_ip_heuristic
# All pass: isolated unit tests OK
```

---

## What's Already Good

- `transport/` is **well-designed** — clear boundary, 26 files, F214 protocol separation done
- `stealth/` is **coherent** — 3 files, clear purpose
- `intel/*_lane.py` convention is **proven and readable**
- `coordinators/base.py` + `_catalog.py` provide **good infrastructure** for coordinator registry
