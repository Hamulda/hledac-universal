# Acquisition Strategy Layer — Architecture Reference

> **Source:** `runtime/acquisition_strategy_planner.py` and `runtime/scheduler/lanes/__init__.py`
> (extracted 2026-07-29). Extracted from module `"""..."""` blocks to keep in-file docstrings ≤ 30 lines.

---

## Role

Dual-role module combining **admission planning** and **lane execution**:

1. **PLANNER:** `build_acquisition_plan()` emits bounded per-lane plans (no I/O)
2. **RUNNER:** `run_enabled_acquisition_lanes()` executes lane adapters with network access and graph/DB accumulation

---

## Planner Section (~lines 40–1723)

Pure, NO network I/O:

- `build_acquisition_plan()` / `_build_plan_impl()`
- `DOMAIN_EXPANSIONS`, `_THREAT_DICTIONARY` lookup
- Lane planning, eligibility, budget, mission intent
- Pure dict/set/tuple manipulation
- **ZERO** network access, **ZERO** model load, **ZERO** asyncio

### Planner Invariants (GHOST_INVARIANTS)

| Invariant | Value |
|-----------|-------|
| No network I/O | ✓ |
| No model/MLX load | ✓ |
| No `asyncio.run()` / `loop.run_until_complete()` | ✓ |
| Bounded: max 12 lanes in plan | ✓ |
| Fail-soft: returns minimal snapshot on any error | ✓ |
| Deterministic: same inputs always produce same plan | ✓ |

---

## Runner Section (~lines 1734–2181)

Has network I/O:

- `run_enabled_acquisition_lanes()` — async, invokes network adapters
- Nested async closures: `_run_ct_lane`, `_run_wayback_lane`, `_run_pdns_lane`,
  `_run_doh_lane`, `_run_blockchain_lane`, `_run_ipfs_lane`, etc.
- `DOHAdapter` via `async_get_httpx_session()` — HTTP fetch (line 2027–2029)
- All lane adapters (crtsh, wayback, passive_dns, shodan, censys, etc.)

### Runner Invariants

| Invariant | Value |
|-----------|-------|
| `gather(return_exceptions=True)` — one lane crash never fails others | ✓ |
| Per-lane `asyncio.timeout` enforced | ✓ |
| STEALTH never auto-enabled | ✓ |
| No MLX/model load | ✓ |

---

## Lanes

| Lane | Description |
|------|-------------|
| `FEED` | Structured TI feeds (always allowed unless hardware critical) |
| `PUBLIC` | Public discovery pipeline |
| `CT` | Certificate transparency log discovery |
| `WAYBACK` | Wayback Machine archive enumeration |
| `PASSIVE_DNS` | Passive DNS lookup |
| `BLOCKCHAIN` | Blockchain analyzer (wallet/hash/crypto indicators) |
| `STEALTH` | Stealth/dark web (disabled by default) |
| `PIVOT_EXECUTOR` | Pivot-driven domain/IP expansion |

### Lane Strategy Rules

| Lane | Rule |
|------|------|
| `FEED` | Always unless hardware critical |
| `PUBLIC` | Unless transport degraded or hardware critical |
| `CT` | Domain-like query OR aggressive mode |
| `WAYBACK` | Query has URL/domain OR enough budget (duration ≥ 300s) |
| `PASSIVE_DNS` | Query has domain/IP indicator |
| `BLOCKCHAIN` | Query has wallet/hash/crypto indicator |
| `STEALTH` | Disabled by default unless explicit flag AND transport phase ≥ breaker_seam |
| `PIVOT_EXECUTOR` | Always allowed (lightweight, advisory) |

- Concurrency reduced under UMA warn/critical
- Heavy optional lanes hard-disabled under swap/critical

---

## F350M-R Cleanup

- Removed duplicate lane runners (CT/WAYBACK/PDNS were defined twice)
- Removed dead helper converters (`_hits_to_ct_findings`, `_ips_to_pdns_findings`,
  `_wallet_to_findings` — no callers anywhere in codebase)

## See Also

- `runtime/scheduler/lanes/__init__.py` — canonical lane definitions
- `runtime/acquisition_strategy_planner.py` — full planner + runner
