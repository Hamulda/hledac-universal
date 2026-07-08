# ISSUE-043: COMPLETED

## Implementované změny

### 1. `compat/core_http.py` — FIXED
- **Před:** Každé volání vytvářelo nový `httpx.AsyncClient` (connection pool discard)
- **Po:** Používá `session_pool.httpx()` singleton (connection pool reuse)
- **CB:** Přidána circuit breaker ochrana přes `domain_breaker_check/record_*`

### 2. `advanced_web/stealth_browser.py` — FIXED
- **Před:** Sync `httpx.Client` v async kontextu, zabalený v `asyncio.to_thread`
- **Po:** Async `httpx.AsyncClient` přes `session_pool.httpx()` — bez `to_thread`
- **CB:** Přidána circuit breaker ochrana

## Co NEOBSAHUJE (původní issue byl zavádějící)

- `intel/bgp_monitor.py` — **už async + CB** ✓
- `intel/passive_dns.py` — **už async + CB** ✓
- `intel/dns_tunnel_detector.py` — **žádné HTTP** ✓
- `tools/registry.py` — **žádné HTTP** ✓

## Testování
- Syntax OK: oba soubory compilují
- Import OK: oba moduly se importují bez chyb
- Pre-existující failures v http3/sprint_scheduler nesouvisí s těmito změnami

## Invarianty
| Test | Soubor | Stav |
|------|--------|-------|
| CB-01: `domain_breaker_check()` vrací `CircuitDecision` | stealth_browser.py | ✓ |
| CB-02: `domain_breaker_record_success()` volán po 2xx | stealth_browser.py | ✓ |
| CB-03: `domain_breaker_record_failure()` při chybě | stealth_browser.py | ✓ |
| SS-01: `session_pool.httpx()` vrací `httpx.AsyncClient` | compat/core_http.py | ✓ |
| ASYNC-01: Žádné `with httpx.Client` bez `to_thread` | stealth_browser.py | ✓ Opraveno |
