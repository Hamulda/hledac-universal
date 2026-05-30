# RL Quality Log — Sprint F235

## Changes

### _compute_reward() field fixes (F235-A)
- `runtime_seconds` → `actual_duration_s` (float, seconds)
- `findings_total` removed → `total_in = findings_accepted + findings_deduplicated`
- Added `_get_finding_count(result, prefix)` helper with fallback chain:
  `findings_accepted` → `findings_produced` → `findings_ingested`
- Dark web sources (tor/i2p/nym/dht) now use fallback chain instead of hardcoded `_accepted` suffix
- ipfs directly: `(getattr(result, 'ipfs_findings_accepted', 0) or 0) * 0.3`
- gopher via `_get_finding_count` (falls back to `findings_ingested`)
- Removed duplicate `total_in` redefinition on F235 quality metrics line
- Clamp: `[-3.0, 5.0]`

### pivot_map trimmed (F235-C)
- Removed entry 5: `deep_hypothesis` (not a real action with 5 agents)
- Map: {0: standard, 1: dark_surface, 2: gopher, 3: bgp_enrichment, 4: academic}

## Pre-fix reward (broken)

```python
# runtime_seconds → always 0 → time_penalty = 0 → reward inflated
# findings_total → always 0 → source_quality_mult = 2.0 always → reward inflated
# Dark web loop (tor/i2p/nym/dht/gopher) → always 0 (fields don't exist)
```

**Example pre-fix reward for MockResult (10 accepted, 5 dedup, 3600s, 2 cycles):**
```
base: log(11)*2.0 - 0 + cycles_bonus = 2.3979 + 0.2 = 2.598
dark_web: 0 (all missing)
dedup bonus: 0.666 * 0.5 = 0.333 (but total_in was redefined so this was double-computed)
bgp: 0.6, cover: 0.5, contradict: -0.2, cb: -0.1
Total: ~4.2 (clamped at 5.0 — same as post-fix but for wrong reasons)
```

The pre-fix result happened to be similar for high-accepted cases (reward saturated at 5.0),
but the signal was broken: no time penalty, inflated source_quality_mult,
no actual dark web or gopher bonuses.

## Post-fix reward (correct)

**Test mock (10 accepted, 5 dedup, 3600s, 2 cycles, ipfs=2, gopher_ingested=3, bgp=3, cover=5, contradict=1, cb=1):**
```
reward = 5.000  clamp: True ✅
```

| Scenario | Reward |
|----------|--------|
| no dedup (10/10) | 5.000 |
| high dedup (1/10) | 2.601 |
| contradictions=5 | 5.000 (clamped) |
| captcha=10 | 5.000 (clamped) |
| gopher_ingested=5 | 5.000 (clamped) |

## suggest_next_pivot()

- Returns `list[dict]` with `pivot_type`, `confidence`, `reason`
- Uses `_state_extractor.extract()` → `agent.q_net(state)` argmax over 5 agents
- Wired in sprint_scheduler.py teardown at line ~7078
- Persists to `result.rl_suggested_pivot`
- Log INFO when `pivot_type == "dark_surface"`

## New SprintSchedulerResult fields (F235)

| Field | Default | Purpose |
|-------|---------|---------|
| `findings_deduplicated` | 0 | dedup efficiency signal |
| `hypothesis_contradictions_detected` | 0 | evidence conflict penalty |
| `cover_traffic_fired` | 0 | OPSEC hygiene bonus |
| `captcha_hits` | 0 | aggressive crawl penalty |
| `circuit_breaker_opens` | 0 | transport failure penalty |
| `rl_suggested_pivot` | "" | last RL pivot decision |

**Note**: `findings_deduplicated` field exists in schema but is not yet populated during
sprint teardown. `_compute_reward` reads 0 until a sidecar wires the dedup count
into the result. This is a known gap — tracked separately.