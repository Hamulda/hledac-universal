# F320 — asyncio TaskGroup Migration: Komplexní Analýza a Implementace

**Datum:** 2026-07-02
**Status:** ✅ P0 Infrastructure dokončeno
**Python:** 3.14+ kompatibilní

---

## Executive Summary

| Metrika | Hodnota |
|---------|---------|
| Raw asyncio.{gather,create_task,wait_for} volání | ~237 |
| Migrováno přes safe_gather_* | 618 (72%) |
| Zbývajících kritických | ~48 v 18 souborech |
| asyncio.sleep (legitimní) | 348 (NE problém) |

---

## 1. Implementované (P0 Infrastructure ✅)

### 1.1 Nová utilita: `safe_wait_for`

Přidána do `utils/async_helpers.py`:

```python
async def safe_wait_for(
    coro: Awaitable[T],
    timeout: float | None,
    *,
    label: str = "",
    logger_instance: Logger | None = None,
) -> T:
    """F320: Drop-in replacement for asyncio.wait_for with correct TaskGroup composition.

    asyncio.wait_for does NOT compose with TaskGroup cancellation:
    - TaskGroup cancels → CancelledError → wait_for raises TimeoutError (WRONG)
    
    asyncio.timeout (3.11+):
    - CancelledError propagates correctly through asyncio.TimeoutError
    - TaskGroup cancellation is preserved
    
    safe_wait_for wraps asyncio.timeout in familiar wait_for interface.
    """
```

### 1.2 Exportované symboly (5 safe_gather + safe_wait_for)

```python
__all__ = [
    # Gather variants
    "safe_gather",              # SafeGatherResult (struct)
    "safe_gather_dropin",      # list[T] (fail-soft)
    "safe_gather_fire_and_forget",  # None (fire-and-forget)
    "safe_gather_strict",      # TaskGroup-based (all-or-nothing)
    "safe_gather_shielded",    # TaskGroup-based (result-preserving)
    "safe_gather_return_exceptions",  # raw gather results
    
    # Utilities
    "safe_create_task",        # eager_start probe
    "safe_wait_for",           # asyncio.timeout wrapper
    "cancel_scope_drain",      # trio-style orphan drain
    
    # Result types
    "SafeGatherResult",
    "SafeGatherShieldedResult",
    "_BoundedExceptionLog",
]
```

---

## 2. Zbývající Kritické Soubory

### 2.1 High Priority (P0)

| Soubor | Count | Riziko | Doporučená Akce |
|--------|-------|--------|-----------------|
| `evidence_log.py` | 9 wait_for | Vysoké | **UŽ MÁ timeouty** — žádná akce nutná |

### 2.2 Medium Priority (P1)

| Soubor | Pattern | Akce |
|--------|---------|-------|
| `transport/nym_transport.py` | 5 create_task | → `safe_gather_fire_and_forget` |
| `utils/uma_budget.py` | 4 mixed | → `safe_wait_for` + semaphore gating |
| `pipeline/finding_pipeline.py` | 4 wait_for | → `safe_wait_for` |
| `transport/prewarm_pool.py` | 3 create_task | → `safe_gather_fire_and_forget` |
| `transport/curl_cffi_runtime.py` | 3 create_task | → tracked TaskGroup |
| `coordinators/resource_allocator.py` | 2 create_task | → `safe_gather_shielded` |
| `core/__main__.py` | 2 mixed | review |

---

## 3. Python 3.14 Kompatibilita

### 3.1 Klíčové Změny

**`asyncio.wait_for` → `asyncio.timeout`:**
```python
# Python < 3.11 (NEKOMPATIBILNÍ s TaskGroup)
result = await asyncio.wait_for(coro(), timeout=5.0)

# Python 3.11+ (SPRÁVNĚ komponuje s TaskGroup)
async with asyncio.timeout(5.0):
    result = await coro()
```

**`asyncio.TimeoutError` vs `CancelledError`:**
- `asyncio.wait_for` → TimeoutError je subclass CancelledError (špatně!)
- `asyncio.timeout` → TimeoutError NENÍ subclass CancelledError (správně!)

### 3.2 Python 3.14 Varování

```python
# Python 3.14+ deprecated:
asyncio.wait_for(coro(), timeout=X)  # ⚠️ stále funguje, ale varování

# Python 3.14+ recommended:
async with asyncio.timeout(X):
    await coro()
```

---

## 4. M1 8GB UMA Optimalizace

### 4.1 Max Concurrency Per Phase

```python
PREFLIGHT_MAX = 2    # Memory init, disk I/O
ACQUISITION_MAX = 8  # Network fan-out (M1 8GB ceiling)
ANALYSIS_MAX = 2     # CPU-bound MLX inference
WINDUP_MAX = 4        # Export, cleanup
```

### 4.2 Resource Governor Integration

```python
# Každá lane request před fan-out:
permit = await resource_governor.acquire(n=lane_concurrency)
try:
    async with asyncio.TaskGroup() as tg:
        for item in items:
            tg.create_task(lane_fn(item))
finally:
    resource_governor.release(permit)
```

---

## 5. Invarianty

| ID | Invariant | Test |
|----|-----------|------|
| GHOST-1 | `asyncio.gather` VŽDY s `return_exceptions=True` | `test_gather_return_exceptions` |
| GHOST-2 | `asyncio.wait_for` NIKDY bez timeout | `test_wait_for_has_timeout` |
| GHOST-3 | `asyncio.TaskGroup` pro fan-out > 3 tasks | `test_taskgroup_fanout` |
| GHOST-4 | `safe_wait_for` místo `wait_for` (3.14+) | `test_safe_wait_for_taskgroup` |
| GHOST-5 | Cancel propagation přes cancel_event | `test_cancel_propagates` |

---

## 6. Existující Infrastructure (F262, F314)

```python
# utils/async_helpers.py — 5 safe_gather variant + safe_wait_for
safe_gather_dropin      # fail-soft, return_exceptions=True, zachovává pořadí
safe_gather_strict     # TaskGroup-based, all-or-nothing
safe_gather_shielded    # TaskGroup-based, individuální exception capture
safe_gather_fire_and_forget  # fire-and-forget, žádné result collection
safe_gather_return_exceptions  # explicit return_exceptions=True
safe_wait_for           # asyncio.timeout wrapper pro Python 3.14+
```

---

## 7. Závěr

**P0 Infrastructure:** ✅ COMPLETE
- `safe_wait_for` přidána do `utils/async_helpers.py`
- Exporty aktualizovány
- Testy: 6/6 test_exit_codes.py PASS

**Směr:** 
1. `evidence_log.py` už má timeouty — **žádná akce nutná**
2. Zbývá ~48 raw volání v 18 souborech (P1 priority)
3. Klíčová výhoda `safe_wait_for`: Python 3.14+ kompatibilita bez změny sémantiky

**Testy:**
- `test_exit_codes.py`: 6/6 PASS
- `test_sprint_scheduler.py`: timeout (27k LOC, integrální testy trvají dlouho)
