# SHIMS ACTIVATION LOG
**Date:** 2026-05-24
**Sprint:** F214Q — Broken Imports & Shim Wiring

---

## Scope

Analyzovány broken importy z `broken_imports.json` v adresářích `knowledge/`, `coordinators/`, `core/`.
10 broken importů nalezeno — všech 10 kryto existujícím fail-soft try/except.
Dodatečný úkol: prověřit `OperationTrackingMixin` v `_catalog.py`.

---

## OperationTrackingMixin — Už SPRÁVNĚ

**Zjištění:** `_catalog.py:59` mapuje `'OperationTrackingMixin': '.base'`. V `base.py:109`
je implementace inline. `mixins.py` neexistuje a není potřeba.

**Akce:** Žádná — správně nasměrováno.

---

## A) PQ Crypto — Wire do security_coordinator.py

**File:** `coordinators/security_coordinator.py:165-176`

**Změna:**
```python
# Původně (broken import):
from hledac.security.quantum_resistant_crypto import QuantumResistantCrypto
self._quantum_crypto = QuantumResistantCrypto()
if hasattr(self._quantum_crypto, 'initialize'):
    await self._quantum_crypto.initialize()

# Nyní (správně):
from hledac.universal.security.pq_crypto import create_post_quantum_backend
from hledac.universal.security.pq_crypto import PQAvailability
self._pq_backend, pq_status = await create_post_quantum_backend(enabled=True, key_id="hledac.security.v1")
self._crypto_available = pq_status.availability.value in ("available", "signed", "fail_soft")
self._pq_crypto_available = self._crypto_available
```

**Důvod:** `pq_crypto.py` obsahuje živou implementaci ML-DSA-65 (PostQuantumBackend Protocol,
SwiftPostQuantumBackend / NullPostQuantumBackend). `create_post_quantum_backend()` je async factory
s fail-soft sémantikou — vždy vrací (backend, status), nikdy nepadá.

**Rozhraní `_execute_crypto_operation`** přepsáno na přímé volání `PQ backend.has_mldsa()`
a `backend.pq_status()` místo neexistující `perform_secure_operation()`.
Guard `if not self._pq_backend` nahrazen za `if pq_status.availability == PQAvailability.DISABLED`
— NullPostQuantumBackend je truthy, starý guard ho neodchytl.

**Bugfix po inicializaci:** `get_post_quantum_backend` → `create_post_quantum_backend`
( správné jméno async factory). Přidán `await` na volání.

---

## B) StealthEngine Alias

**Soubor:** `_shims/security_stealth_engine.py` (nahrazen NotImplementedError stub)

**Změna:**
```python
# Původně: raise NotImplementedError("StealthEngine stub — real implementation missing")

# Nyní: adapter wrapping hledac.universal.stealth.stealth_session.StealthSession
class StealthEngine:
    def __init__(self):
        from hledac.universal.stealth.stealth_session import StealthSession
        self._session = StealthSession()

    async def activate_stealth_mode(self, operation_type, confidence_threshold, security_level):
        await asyncio.sleep(random.uniform(jitter_min, jitter_max))
        ua = self._session.rotate_ua()
        return {'active': True, 'success': True, 'measures_activated': 1, 'ua_used': ua[:60]}
```

**Důvod:** `StealthSession` (stealth/stealth_session.py) poskytuje JA3+UA rotation.
`SecurityCoordinator._execute_stealth_operation` očekává `activate_stealth_mode()`.
Adapter převádí mezi oběma API.

**Import path v security_coordinator.py:** `from hledac.security.stealth_engine import StealthEngine`
(přes `_shims/security_stealth_engine.py` re-export).

---

## C) Watchdog Alias — core_watchdog.py

**Soubor:** `_shims/core_watchdog.py` (nahrazen NotImplementedError stub)

**Změna:**
```python
# Původně: raise NotImplementedError("Watchdog stub — real implementation missing")

# Nyní: adapter wrapping hledac.universal.utils.uma_budget.UmaWatchdog
class Watchdog:
    def __init__(self, threshold_mb=None, check_interval=None, callback=None):
        from hledac.universal.utils.uma_budget import UmaWatchdog, UmaWatchdogCallbacks
        callbacks = UmaWatchdogCallbacks(on_warn=callback, on_critical=callback) if callback else None
        self._impl = UmaWatchdog(callbacks=callbacks, interval=check_interval or 0.5)

    async def start(self): self._impl.start(); self._running = True
    async def stop(self): self._impl.stop(); self._running = False
```

**Důvod:** `UmaWatchdog` (utils/uma_budget.py:486) je produkční memory watchdog.
`UmaWatchdog.stop()` je `def stop(self) → None` (sync), ne async.
Adapter volal `await self._impl.stop()` — opraveno na sync volání.

**Bugfix:** `await self._impl.stop()` → `self._impl.stop()` — stop() je sync.

---

## Co NEByLO IMPLEMENTOVÁNO

| Item | Důvod |
|------|-------|
| `core_unified_ai_orchestrator` | Legacy, nedrátujeme |
| `cortex_director` | Neznámý scope, stub správně |
| `security_zkp_research_engine` | ZKP bez reálné impl, stub správně |
| `core_http` | Nikde neimportován, ponechán |
| `security_threat_intelligence` | Žádná reálná impl v universal/security/automation/ |

---

## CI Health Check

```bash
$ python scripts/ci_health_check.py
OK: SprintScheduler
OK: DuckDBShadowStore
OK: FetchCoordinator

All CI health checks passed. ✅
```

---

## Zbývající Pyright Missing Imports (neblocking)

Jsou to stejné broken imports z `broken_imports.json` — legacy hledac.* moduly
(mimo universal/), fail-soft handlery v koordinátorech. Ne blocking.

---

**Generated:** 2026-05-24 | Sprint F214Q