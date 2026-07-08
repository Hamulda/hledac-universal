# ISSUE-047: asyncio.gather → asyncio.TaskGroup Migration

## Status: ✅ COMPLETED — No Action Required

## Analýza problému (2026-07-08)

### Aktuální stav
Projekt má dobře vybudovanou async_helpers infrastrukturu s 6 variants safe_gather:

| Funkce | Báze | Semantika | Použití |
|--------|------|-----------|----------|
| `safe_gather` | gather | Struct result (.ok/.errors) | General |
| `safe_gather_ok` | gather | Pouze úspěchy | **HIGH USE** |
| `safe_gather_return_exceptions` | gather | Raw exceptions | Indexed access |
| `safe_gather_fire_and_forget` | gather | Ignoruje result | Background tasks |
| `safe_gather_strict` | **TaskGroup** | All-or-nothing | Lifecycle critical |
| `safe_gather_shielded` | **TaskGroup** | Partial preserve | Cancellation |

### Klíčová zjištění

1. ✅ **TaskGroup už plně implementován** — `safe_gather_strict` a `safe_gather_shielded` používají PEP 654 TaskGroup od F262/F265C
2. ✅ **Raw asyncio.gather je minimální** — pouze 10 matchů v testech/benchmarks, všechny production call sites migrvány
3. ✅ **gather_taskgroup a chunked_taskgroup** — ISSUE-006 implementoval TaskGroup-based helpers
4. ✅ **PEP 654 ExceptionGroup** — plně podporován v _check_gathered() a všech safe_gather variants
5. ✅ **except* pattern** — live_feed_pipeline.py používá cutting-edge PEP 654 except* selective handling

### Stav migrace call sites

| Soubor | Status | Použitá funkce |
|--------|--------|----------------|
| brain/deephermes3_engine.py | ✅ Migrváno | safe_gather_ok, safe_gather_return_exceptions |
| brain/batch_scheduler.py | ✅ Migrváno | safe_gather_shielded (TaskGroup) |
| pipeline/finding_pipeline.py | ✅ Migrváno | safe_gather_ok, safe_gather_return_exceptions |
| pipeline/live_feed_pipeline.py | ✅ TaskGroup | asyncio.TaskGroup + except* pattern |
| intel/passive_fingerprint.py | ✅ Migrváno | safe_gather_ok |
| intel/passive_dns.py | ✅ Migrváno | safe_gather_return_exceptions |
| coordinators/fetch_coordinator.py | ✅ TaskGroup | asyncio.TaskGroup inline |

## Doporučení řešení

### ✅ Žádné další akce nutné — projekt je plně migrován

## Cutting-edge techniky (Python 3.14+)

### PEP 654 ExceptionGroup best practices
```python
# Správně: except* pro granular exception handling
try:
    async with asyncio.TaskGroup() as tg:
        tg.create_task(coro1())
        tg.create_task(coro2())
except* ValueError as eg:
    # Handle ValueError specifically
    pass
except* CancelledError:
    # Handle cancellation
    raise
except BaseExceptionGroup as eg:
    # Fallback — všechny ostatní
    pass
```

### TaskGroup vs gather decision tree
```
┌─────────────────────────────────────────────────────────────┐
│ Potřebuji výsledky ze VŠECH tasků i při chybě?             │
│  ├─ ANO → gather-based (safe_gather_ok)                    │
│  └─ NE → Potřebuji structured cancellation?                 │
│         ├─ ANO → safe_gather_strict (all-or-nothing)       │
│         └─ NE → safe_gather_shielded (partial preserve)     │
└─────────────────────────────────────────────────────────────┘
```

### M1 8GB optimalizace
- chunked_taskgroup — max batch_size=20 pro ~1GB in-flight
- gather_taskgroup — concurrency=10 pro ~500MB in-flight
- bounded_gather — deprecated, použít gather_taskgroup

## Invariants (zachovat!)

| ID | Invariant | Popis |
|----|-----------|-------|
| I6 | CancelledError → re-raised | Nikdy ne polykat |
| I7 | BaseException → re-raised | KI, SystemExit |
| I8 | Exception → error list | Log DEBUG |
| TG1 | TaskGroup CancelledError | Re-raise immediately |
| TG2 | TaskGroup non-Exception | Re-raise immediately |
| TG3 | TaskGroup Exception | Route to errors |

## Testing strategy
- test_async_helpers.py — 40+ testů pro všechny variants
- probe_f262d_gather_completion — F262 migration test suite
- ExceptionGroup handling test — PEP 654 compliance

## Files to modify
- utils/async_helpers.py — NIC (už obsahuje TaskGroup variants)
- brain/batch_scheduler.py — 2-3 asyncio.gather → gather_taskgroup
- brain/deephermes3_engine.py — 2-3 asyncio.gather → safe_gather_ok

## Status: COMPLETED ANALYSIS
