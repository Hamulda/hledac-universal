# ISSUE-043: requests/urllib → httpx (async) — ANALYZA

## Stav: NUTNÉ PŘE品AZENÍ (viz sekce "Skutečný stav")

---

## 1. Skutečný stav codebase

### requests NENÍ používán
```
$ grep -rn "^import requests\|^from requests" --include="*.py" . | grep -v ".venv\|site-packages"
(no output)
```
**Závěr:** Přímý `import requests` v projektu **neexistuje**. `requests` knihovna není vůbec používána.

### urllib.parse JE používán (ale pouze pro URL PARSING, ne HTTP)
```
intel/gemini_transport.py:import urllib.parse          # quote()
pipeline/live_public_pipeline.py:import urllib.parse
transport/httpx_transport.py:import urllib.parse
transport/curl_cffi_fetch.py:import urllib.parse
coordinators/fetch_coordinator.py:from urllib.parse import urlparse  # F3XX: httpx.URL replaces urllib.parse.urlparse
```
**Závěr:** `urllib.parse` se používá **pouze** pro parsování URL, **ne** pro HTTP requesty. Toto není problém — jde o standardní Python stdlib bez I/O.

---

## 2. Analýza identifikovaných souborů

### `intel/bgp_monitor.py`
- **Async:** `async def monitor_bgp()` — plně async
- **HTTP:** `aiohttp` přes `async_get_aiohttp_session()` / `_ipfs_checked_get()`
- **Blocking:** `asyncio.to_thread(_stream_events)` — správně izoluje blocking SSE streaming
- **Circuit breaker:** ANO — používá `circuit_breaker` pattern, `_ipfs_checked_get` je CB-wrapped
- **Verdikt:** ✅ Async-nativní, CB-wrapped, NENÍ potřeba migrace

### `intel/passive_dns.py`
- **Async:** `async def _do_query()` — plně async
- **HTTP:** `aiohttp.ClientSession` přes `_ensure_session()`
- **Circuit breaker:** ANO — `checked_aiohttp_get` z `circuit_breaker.py`
- **Verdikt:** ✅ Async-nativní, CB-wrapped, NENÍ potřeba migrace

### `intel/dns_tunnel_detector.py`
- **Async:** `async def initialize()`, `async def analyze_queries()`, `async def _analyze_single_query()`
- **HTTP:** DNS tunneling detector nepoužívá HTTP — analyzuje DNS query vzory
- **Circuit breaker:** N/A — žádné externí HTTP volání
- **Verdikt:** ✅ Ne-používá HTTP vůbec

### `tools/registry.py`
- **HTTP:** ŽÁDNÉ HTTP volání
- **Funkce:** Čistá registrace a discovery toolů
- **Verdikt:** ✅ Žádný HTTP kód

### `advanced_web/stealth_browser.py`
- **Sync `httpx.Client`:** Na řádku 331 `with httpx.Client(...)` — **JEDINÝ sync httpx.Client v projektu**
- **Volá se přes:** `asyncio.to_thread(_sync_fetch)` — **správně**, neblokuje event loop
- **Circuit breaker:** NE — fallback path, voláno z `_fetch_httpx` v nodriver fallbacku
- **Verdikt:** ⚠️ Malý problém — sync `httpx.Client` v async kontextu, ale OBRANNĚ zabalen do `asyncio.to_thread`

---

## 3. Existující infrastruktura

### `transport/session_pool.py` (420 L)
```python
class SessionPool:
  async def httpx(self) → httpx.AsyncClient      # HTTP/2 singleton
  async def httpx_socks(self, proxy_url)       # SOCKS5 proxy client
  async def curl_cffi(self, profile)           # curl_cffi session
```

**Použití:**
```python
from transport.session_pool import session_pool
client = await session_pool.httpx()  # získat sdílený HTTP/2 client
```

### `transport/circuit_breaker.py` (778 L)
```python
async def checked_aiohttp_get(session, url, *, timeout, failure_kind) → tuple
async def checked_aiohttp_post(session, url, *, json, timeout, failure_kind) → tuple
def domain_breaker_check(domain) → CircuitDecision
def domain_breaker_record_success(domain)
def domain_breaker_record_failure(domain, is_timeout, failure_kind)
```

**Integrováno:** `intel/bgp_monitor.py`, `intel/passive_dns.py`, `coordinators/fetch_coordinator.py`

### `compat/core_http.py`
```python
async def fetch_json(url, timeout) → dict
async def safe_fetch(url, timeout) → dict | None
```
**Problém:** Každé volání vytváří NOVÝ `httpx.AsyncClient` — není to session pool! Toto JE problém.

---

## 4. Skutečné problémy k řešení

### P0: `compat/core_http.py` — nový client per request
```python
# ŠPATNĚ — vytváří nový client každé volání
async with httpx.AsyncClient(timeout=timeout) as client:
    resp = await client.get(url, **kwargs)
```
**Řešení:** Použít `session_pool.httpx()`:
```python
from transport.session_pool import session_pool
client = await session_pool.httpx()
resp = await client.get(url, **kwargs)  # reuse connection pool
```

### P1: `advanced_web/stealth_browser.py` řádek 331
```python
# AKTUÁLNĚ (obaleneno asyncio.to_thread — není kritický bug)
with httpx.Client(headers=headers, timeout=_FETCH_TIMEOUT, follow_redirects=True) as client:
    response = client.get(url)
```
**Řešení:** Přepsat na `httpx.AsyncClient` a volat přímo bez `to_thread`:
```python
async with httpx.AsyncClient(...) as client:
    response = await client.get(url)
```

### P2: `urllib.parse` → `httpx.URL` (fetch_coordinator.py už migrován)
Některé soubory stále používají `urllib.parse.urlparse` kde už je `httpx.URL` k dispozici:
- `intel/gemini_transport.py:25` — `urllib.parse.quote(query)` — **OK** (standard library, žádný I/O)
- `pipeline/live_public_pipeline.py:25` — **ZKONTROLOVAT** zda jde o parsování nebo I/O
- `transport/httpx_transport.py:30` — už importuje `urllib.parse` vedle `httpx` — možná duplikace

---

## 5. Doporučené řešení

### Krok 1: Oprav `compat/core_http.py`
```python
# Před
async with httpx.AsyncClient(timeout=timeout) as client:
    resp = await client.get(url, **kwargs)

# Po — použít session pool
from transport.session_pool import session_pool
client = await session_pool.httpx()
resp = await client.get(url, timeout=timeout, **kwargs)
```

### Krok 2: Migrace `stealth_browser.py` na async httpx
```python
# Před (sync)
def _sync_fetch() -> tuple[int, str]:
    with httpx.Client(...) as client:
        response = client.get(url)
        return response.status_code, response.text

# Po (async)
async def _async_fetch() -> tuple[int, str]:
    async with httpx.AsyncClient(...) as client:
        response = await client.get(url)
        return response.status_code, response.text
```

### Krok 3: Přidat circuit breaker do `stealth_browser.py`
```python
from transport.circuit_breaker import domain_breaker_check, domain_breaker_record_success, domain_breaker_record_failure
from urllib.parse import urlparse

domain = urlparse(url).netloc
decision = domain_breaker_check(domain)
if not decision.allowed:
    return self._error_result(url, f"circuit_breaker_open:{decision.reason}")
try:
    result = await _async_fetch()
    domain_breaker_record_success(domain)
except Exception as e:
    domain_breaker_record_failure(domain, failure_kind=str(e))
```

### Krok 4: Volitelné — `urllib.parse` → `httpx.URL` parsování
```python
# Před
from urllib.parse import urlparse
parsed = urlparse(url)
domain = parsed.netloc

# Po
import httpx
parsed = httpx.URL(url)
domain = parsed.host
```
**Poznámka:** Toto je kosmetická změna — `urllib.parse` je stdlib a nemá I/O overhead. Jen pro konzistenci s `fetch_coordinator.py` (už migrováno).

---

## 6. Invarianty (testovatelnost)

| Test | Soubor | Ověření |
|------|--------|---------|
| CB-01: `domain_breaker_check()` vrací `CircuitDecision` | `stealth_browser.py` | Ano |
| CB-02: `domain_breaker_record_success()` volán po 2xx | `stealth_browser.py` | Ano |
| CB-03: `domain_breaker_record_failure()` volán při chybě | `stealth_browser.py` | Ano |
| SS-01: `session_pool.httpx()` vrací `httpx.AsyncClient` | `compat/core_http.py` | Ano |
| SS-02: Žádný nový `httpx.AsyncClient()` mimo session_pool | `compat/core_http.py`, `stealth_browser.py` | Ano |
| ASYNC-01: Žádného `with httpx.Client` bez `asyncio.to_thread` | `stealth_browser.py` | Opraveno |

---

## 7. Scope migrace (editace)

| Soubor | Změna | Priorita |
|--------|--------|----------|
| `compat/core_http.py` | `httpx.AsyncClient` per-request → `session_pool.httpx()` | P0 |
| `advanced_web/stealth_browser.py` | sync `httpx.Client` → async `httpx.AsyncClient` + CB | P1 |

**Není potřeba migrovat:**
- `intel/bgp_monitor.py` — už async + CB
- `intel/passive_dns.py` — už async + CB  
- `intel/dns_tunnel_detector.py` — žádné HTTP
- `tools/registry.py` — žádné HTTP

---

## 8. M1 8GB RAM implikace

- `httpx.AsyncClient` HTTP/2: ~5-10 MB per session (pooled connection reuse)
- Nový client per request: connection pool discards → více memory churn
- `session_pool.httpx()` singleton: 1 client sdílený napříč celým procesem → **úspora RAM**
- `stealth_browser._async_fetch`: async eliminaruje `asyncio.to_thread` overhead (~1-2ms per volání)

---

## 9. Závěr

**Původní issue popis byl ZAVÁDĚJÍCÍ** — `requests` a `urllib` nejsou používány pro HTTP v `intel/` souborech.

**Skutečný problém:**
1. `compat/core_http.py` — vytváří nový `httpx.AsyncClient` na každé volání (místo session pool)
2. `stealth_browser.py` — sync `httpx.Client` v async kontextu (obaleneno `to_thread`, ale ne optimální)

**Infrastruktura pro řešení už existuje:**
- `transport/session_pool.py` — hotový singleton pattern
- `transport/circuit_breaker.py` — hotový CB pattern
- `compat/core_http.py` — malá oprava

**Odhad práce:** 2 soubory, ~30 řádků změn, 1 nový test.
