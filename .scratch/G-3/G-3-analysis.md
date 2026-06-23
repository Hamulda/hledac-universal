# G-3: Hardware Critical Lane Gating — Root Cause Analysis

## System Architecture

### Two Distinct UMA State References

| Reference | Location | Authority | Purpose |
|---|---|---|---|
| `pre_sprint_uma_state` | `core/__main__.py:1002` (result JSON) | `sample_uma_status()` at smoke-check time | Post-sprint classification, smoke verdict |
| `_uma_state` | `runtime/sprint_scheduler.py:7026` | Governor `evaluate()` at runtime | **Actual lane gating decisions** |
| `_uma` | `runtime/sprint_scheduler.py:12970` | Governor `evaluate()` per-cycle | Runtime lane throttling decisions |

### Lane Gating Flow

```
SprintScheduler.run() 
  → _governor.evaluate() → _governor._uma_state (continuously updated)
  → _uma_state passed to build_acquisition_plan() 
  → AcquisitionContext.uma_state → LANE_RULES[].enabled() lambdas
  → AcquisitionLanePlan.enabled for each lane
```

### Key Finding: pre_sprint_uma_state Is NOT Used for Lane Gating

`pre_sprint_uma_state` (from `run_pre_sprint_checks()`) is stored in result JSON ONLY.
It is NEVER passed to `build_acquisition_plan()`. Lane gating uses runtime `_uma_state`.

```python
# core/__main__.py:1002 — pre_sprint_uma_state used ONLY for classification
"pre_sprint_uma_state": uma_state,  # from run_pre_sprint_checks()

# runtime/sprint_scheduler.py:7026 — actual lane gating uses runtime _uma_state
self._acquisition_plan = build_acquisition_plan(
    uma_state=_uma_state,  # ← from governor.evaluate(), NOT pre_sprint_uma_state
    ...
)
```

### Lane Enable Conditions (LANE_RULES in acquisition_strategy.py)

| Lane | Enabled when | Disabled when |
|---|---|---|
| **FEED** | `uma_state not in ("critical","emergency")` | critical/emergency |
| **PUBLIC** | `not hardware_critical and not transport_degraded` (deep_osint_m1 else `uma_state not in ("critical","emergency")` and `not transport_degraded`) | hardware_critical OR no domain (default profile) |
| **CT** | `has_domain or aggressive or nonfeed_diagnostic` AND `not hardware_critical or aggressive` | no domain + not aggressive |
| **DOH** | `has_domain` AND `not hardware_critical or nonfeed_diagnostic or aggressive` | no domain |
| **WAYBACK** | `has_url or has_long_duration or (nonfeed_diagnostic and has_domain)` | no URL + short duration |
| **PASSIVE_DNS** | `has_domain and not hardware_critical or nonfeed_diagnostic or aggressive` | no domain |
| **BLOCKCHAIN** | `has_crypto and not hardware_critical or aggressive` | no crypto or hardware_critical |
| **STEALTH** | `stealth_ready and not hardware_critical or aggressive` | no stealth or hardware_critical |
| **ACADEMIC** | `is_academic and not hardware_critical or aggressive` | not academic profile |
| **OPEN_SOURCE** | `is_academic and not hardware_critical or aggressive` | not academic profile |
| **IPFS** | `cid_present and not hardware_critical or aggressive` | no CID in query |
| **SHODAN** | `has_ip and not hardware_critical or aggressive` | no IP indicator |
| **CENSYS** | env-gated | — |
| **GREYNOISE** | env-gated | — |

### Root Cause of Divergence

The "divergence" is actually BY DESIGN:

1. `pre_sprint_uma_state` is a ONE-TIME snapshot at smoke-check time (before scheduler even starts)
2. `_uma_state` is the LIVE state from the governor, which changes as memory pressure evolves

The real problem is **NOT** that pre_sprint and runtime diverge — it's that:

### The Actual Problem: PUBLIC Lane Disabled for Non-Domain Queries

For the `default` profile:
- `PUBLIC` requires `ctx.has_domain` OR specific conditions
- If query has no domain indicator AND is not aggressive/nonfeed_diagnostic → PUBLIC is **disabled**
- `CT`, `DOH`, `WAYBACK`, `PASSIVE_DNS` also require `has_domain`

So for a CVE/ransomware/etc. query without domain indicator on default profile:
- PUBLIC disabled (no domain, not hardware_critical just no domain)
- CT disabled (no domain, not aggressive)  
- DOH disabled (no domain)
- WAYBACK disabled (no URL, not long duration)
- PASSIVE_DNS disabled (no domain)
- IPFS disabled (no CID)
- → **6+ lanes legitimately disabled**

### What "6 lanes disabled" Means

The `disabled_nonfeed_lanes` field in `nonfeed_plan_debug` shows WHY lanes were disabled, not that they SHOULD have been enabled.

### The Fix Required

The user's concern is that `pre_sprint_uma_state: ok` but 6 lanes are disabled. The issue is likely:

1. **Misunderstanding**: The 6 disabled lanes are NOT because of `uma_state` — they're because the query lacks domain indicators
2. **The "divergence"** the user sees is: `pre_sprint_uma_state = ok` (memory OK at start) but lanes are still disabled (because of query content, not memory)

**However**, there IS a real bug if lanes that SHOULD run (based on `pre_sprint_uma_state`) are disabled due to runtime state being MORE restrictive than at pre-flight.

### Scenario Where This Actually Matters

If at sprint launch memory was `ok` (pre_sprint_uma_state = ok) but runtime memory becomes `critical`:
- `build_acquisition_plan()` at line 7018 uses `_uma_state` which IS the runtime state
- So lanes ARE correctly gated by runtime state
- **This is correct behavior** — runtime state is what matters for execution

### Conclusion

The "6 lanes disabled when pre_sprint_uma_state=ok" is likely:
- PUBLIC disabled because query has no domain indicator (expected for CVE/ransomware queries)
- Not a bug, but a feature of the acquisition planning system
- The `pre_sprint_uma_state` classification is for reporting, not for lane gating

### Recommendation

If the user wants to see WHICH lanes are disabled due to hardware vs query content, we should:
1. Add telemetry distinguishing `hardware_critical` disables from `query_content` disables
2. Or ensure pre_sprint_uma_state is ALSO recorded alongside disabled_nonfeed_lanes for post-mortem analysis
