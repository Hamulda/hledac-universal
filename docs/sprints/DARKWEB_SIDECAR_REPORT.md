# DARKWEB_SIDECAR_REPORT — Sprint F229A

**Date:** 2026-05-23
**Scope:** hledac/universal/ — onion_discovery wiring, Tor/I2P/Nym transport audit

---

## 1. ONION_DISCOVERY — NOT FOUND

`intelligence/onion_discovery.py` **does not exist**.

The `onion_discovery` source_type in the taxonomy maps to existing components:

| File | Class | Role |
|------|-------|------|
| `intelligence/dark_web_intelligence.py` | `DarkWebCrawler` | Standalone Tor/.onion crawler |
| `intelligence/onion_seed_manager.py` | `OnionSeedManager` | Seed list + Ahmia .onion discovery |
| `intelligence/stealth_crawler.py` | `StealthCrawler` | Stealth web crawler (may include .onion) |

**Finding:** `onion_discovery` is NOT wired to the sprint sidecar system. It is standalone.

---

## 2. DARKWEB_INTELLIGENCE — STANDALONE SCANNER

**File:** `intelligence/dark_web_intelligence.py` (695 lines)

### Entry Points
- **Class:** `DarkWebCrawler`
- **Key methods:** `initialize()`, `crawl_onion(url)`, `search_onion_addresses(query)`, `monitor_service()`, `get_statistics()`, `reset_session()`, `close()`

### Tor Usage
- Uses `aiohttp_socks` for SOCKS5 proxy via `ProxyConnector.from_url('socks5://127.0.0.1:9050')`
- Tor availability checked via `TOR_AVAILABLE` flag (import-time check)
- **NO `HLEDAC_ENABLE_TOR` env var gate** — falls back to localhost if Tor unavailable
- Returns: `DarkWebContent` dataclass (not `CanonicalFinding`)
- **NOT integrated into sprint sidecar bus**

---

## 3. ONION_SEED_MANAGER — PARTIALLY INTEGRATED

**File:** `intelligence/onion_seed_manager.py` (211 lines)

### Entry Point
- **Class:** `OnionSeedManager`
- **Discovery method:** `discover_via_tor(query, tor_session)` — Ahmia .onion search via Tor session

### Seeds
- `CURATED_SEEDS` hardcoded: Hidden Wiki + Ahmia onion
- `discover_from_ahmia()` — clearnet fallback
- Persistence: `TOR_ROOT/onion_seeds.json`

### Integration
- **NOT wired to sprint sidecar** — standalone tool, not a sidecar runner
- Used manually to seed onion crawl targets

---

## 4. TOR_TRANSPORT — CIRCUIT ROTATION INCOMPLETE

**File:** `transport/tor_transport.py` (444 lines)

### Controller
- **Class:** `TorTransport` (NOT TorController)
- `is_circuit_established()` — checks Tor daemon + circuit
- **Port:** 9050 SOCKS5, 9051 ControlPort

### Tor Circuit Rotation
- `MAX_CIRCUIT_REQUESTS = 100` — rotate after 100 requests per TorTransport instance
- `rotate_circuit()` at line 256 — calls `self._controller.new_circuit()` if stem available
- `_maybe_rotate_circuit()` at line 280 — increments counter, triggers rotation at threshold
- **Called after every fetch** (line 310: `await self._maybe_rotate_circuit()`)
- **Per-instance counter** — not per-domain. All .onion requests share same counter → correlation risk
- **No per-domain circuit isolation** — `circuits[domain]` dict not implemented

### Fail-Soft
- `available = False` when deps missing → no crash
- Falls back to `localhost` when Tor unavailable

---

## 5. I2P_TRANSPORT — WORKING SAM BRIDGE

**File:** `transport/i2p_transport.py` (428 lines)

### Controller
- **Class:** `I2PTransport`
- **SAM:** `127.0.0.1:7656` (standard I2P SAM bridge)
- `is_running()` — checks I2P session alive

### Status
- **Functional** — fully implemented with fail-soft
- `I2PUnavailableError` when deps missing
- Session: `aiohttp.ClientSession` via socks proxy

### Missing
- **No I2P scan path in sprint sidecar** — I2PTransport exists but no sidecar runner uses it to scan .i2p addresses found in current sprint IOCs

---

## 6. NYM_TRANSPORT — FUNCTIONAL STUB

**File:** `transport/nym_transport.py` (239 lines)

### Controller
- **Class:** `NymTransport`
- **Websocket:** `ws://127.0.0.1:1977`
- Circuit breaker pattern: `circuit_breaker_open`, threshold=3, timeout=60s
- Health check loop every 30s

### Status
- **Functional** — full implementation with reconnect logic
- Requires `nym-client` binary
- `nym_address` obtained from selfAddress message

### Missing
- **Not wired to sprint sidecar** — no Nym mixnet scan path

---

## 7. SPRINT SCHEDULER — ONION_DISCOVERY MISSING FROM SIDECAR CHAIN

**File:** `runtime/sprint_scheduler.py` (~10,000 lines)

### Current Sidecar Chain (via SidecarBus)

**Stage 1** (light extraction):
`leak_sentinel → passive_fingerprint → evidence_triage → temporal_archaeology`

**Stage 2** (correlation):
`exposure_correlator → identity_stitching → sprint_diff → rir_correlator → social_identity_surface → wayback_diff`

**Stage 3** (derived):
`kill_chain_tagging → embedding`

### CT Log Sidecar Position
`_run_ct_log_discovery_in_cycle()` at line ~7495 — stores CT findings first, then sidecar bus fires.

**`onion_discovery` is NOT in the sidecar chain. `dark_web_intelligence` is NOT registered.**

### Search Results
- `onion` mentions in sprint_scheduler: **0**
- `dark_web` references: **0**
- Sidecar registrations: **0** (sidecar bus is configured differently)

---

## 8. GAPS IDENTIFIED

| Gap | Severity | Description |
|-----|----------|-------------|
| onion_discovery not sidecar | HIGH | Dark web discovery not in sprint pipeline |
| Tor circuit rotation not called | HIGH | `MAX_CIRCUIT_REQUESTS=100` constant exists but `rotate_circuit()` never called from fetch path |
| I2P scan path missing | MEDIUM | I2PTransport exists but no sidecar scans .i2p addresses from current sprint IOCs |
| Nym not wired | MEDIUM | NymTransport functional but no scan path |
| HLEDAC_ENABLE_TOR not enforced | MEDIUM | DarkWebCrawler has no env var gate |
| Per-domain circuit isolation missing | MEDIUM | All .onion requests share circuit — correlation attack risk |

---

## 9. RECOMMENDED ACTIONS

### P0 — Wire onion_discovery as conditional sidecar
Add `_run_onion_discovery_sidecar()` after `_run_ct_log_discovery_in_cycle()`:
- Gated by: `HLEDAC_ENABLE_TOR=1`
- Tor connectivity check: `TorTransport.is_circuit_established()`
- Memory pressure < 70%
- Uses `DarkWebCrawler` + `OnionSeedManager`
- Returns `CanonicalFinding` list (needs adapter to convert from `DarkWebContent`)

### P1 — Fix Tor circuit rotation
- Verify `rotate_circuit()` is called at `MAX_CIRCUIT_REQUESTS` in `TorTransport.fetch()`
- Add per-domain circuit isolation: `circuits[domain]` dict tracking
- After 3 requests to same .onion → rotate (not 100 — reduce to 3 for correlation attack prevention)

### P1 — Add I2P scan path
- In `_run_onion_discovery_sidecar` or separate `_run_i2p_discovery_sidecar()`
- For each `.i2p` address in current sprint IOCs: attempt `I2PTransport.fetch()`
- Bound: 5 concurrent, 60s timeout

### P2 — Add Nym mixnet option
- Third anonymity layer for sensitive queries
- Wire similarly to Tor/I2P transport selection

---

## 10. NO CHANGES MADE (Audit Only)

This report confirms wiring status. No code changes applied. All findings are READ-ONLY analysis.

**Evidence:**
- `intelligence/onion_discovery.py` → ENOENT (file does not exist)
- `DarkWebCrawler` → standalone scanner in `dark_web_intelligence.py`, NOT wired to sprint
- `TorTransport.rotate_circuit()` → at line 256, called from `fetch()` at line 310, counter-based at 100/request
- `TorTransport.is_circuit_established()` → confirmed; no `is_connected()` method
- `I2PTransport.is_running()` → confirmed; SAM bridge at 127.0.0.1:7656
- Sidecar chain → searched entire sprint_scheduler.py for "onion" → 0 matches
- `onion_seed_manager.py` → exists, `discover_via_tor()` method present, not wired to sprint
- `NymTransport` → functional stub at 127.0.0.1:1977, websocket-based, circuit breaker implemented