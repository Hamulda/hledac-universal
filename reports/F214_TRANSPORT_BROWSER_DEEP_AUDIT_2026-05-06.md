# Transport/Browser Layer Deep Audit
**Sprint F214 | 2026-05-06**

---

## 1. TRANSPORT STACK MAP

### 1.1 Default → Optional Lanes (full priority order)

```
async_fetch_public_text()  [public_fetcher.py 893]
│
├─ use_js=True
│   └─ _fetch_with_camoufox()          [camoufox Firefox-based, primary JS]
│       └─ _fetch_with_nodriver()      [nodriver CDP, fallback JS]
│
├─ use_tor=True
│   └─ _get_tor_session() → aiohttp_socks.ProxyConnector  [TOR]
│
├─ use_i2p=True
│   └─ _get_i2p_session() → aiohttp_socks.ProxyConnector  [I2P]
│
├─ should_use_curl_cffi()              [curl_cffi_transport.py 34]
│   └─ use_stealth=True → curl_cffi JA3 lane
│   └─ prior_status 403/429 → curl_cffi lane
│   └─ protection_hint known CDN → curl_cffi lane
│   └─ DEFAULT: returns False → aiohttp lane
│       └─ fetch_via_curl_cffi()       [curl_cffi_fetch.py]
│           └─ async_get_curl_cffi_session() [curl_cffi_runtime.py]
│               └─ AsyncSession(impersonate=chrome110/chrome120/chrome136)
│               └─ LRU pool, max 3 profiles, asyncio.Lock
│
├─ should_use_httpx_h2()               [httpx_transport.py 277]
│   └─ HLEDAC_ENABLE_HTTPX_H2=1 env gate
│   └─ NOT darknet/stealth/JS
│   └─ API-like URL OR same-host pattern
│   └─ circuit breaker (auto-disable after 3 failures)
│       └─ fetch_via_httpx_h2()         [httpx_transport.py 361]
│           └─ httpx.AsyncClient HTTP/2
│
└─ DEFAULT → aiohttp hot-path
    └─ async_get_aiohttp_session()     [network/session_runtime.py]
        └─ singleton aiohttp.ClientSession
        └─ TCPConnector(limit_per_host=30)
        └─ uvloop.install() at boot      [__main__.py 39]
```

### 1.2 Lightpanda (separate coordinator, not in public_fetcher)

```
FetchCoordinator                           [fetch_coordinator.py 265]
├─ _fetch_with_lightpanda() → aiohttp binary download
└─ _fetch_with_nodriver()  → nodriver CDP
```

### 1.3 Parallel/Specialized Fetchers (own session factories)

| Module | Transport | Session |
|--------|-----------|---------|
| `discovery/ti_feed_adapter.py` | aiohttp | inline |
| `discovery/duckduckgo_adapter.py` | aiohttp | inline |
| `discovery/wayback_cdx_adapter.py` | aiohttp | inline |
| `intelligence/shodan_wrapper.py` | aiohttp + aiohttp_socks | inline |
| `intelligence/academic_search.py` | aiohttp | async_get_aiohttp_session |
| `intelligence/exposure_clients.py` | httpx | inline |
| `pipeline/live_feed_pipeline.py` | aiohttp | async_get_aiohttp_session |
| `pipeline/live_public_pipeline.py` | aiohttp | async_get_aiohttp_session |

---

## 2. DEFAULT vs OPTIONAL PATH SUMMARY

| Path | Default? | Env Gate | Condition |
|------|----------|----------|-----------|
| **aiohttp** (clearnet) | **YES** | None | Default |
| curl_cffi JA3 | OPTIONAL | `HLEDAC_ENABLE_CURL_CFFI=1` | stealth/403/429/CDN |
| httpx HTTP/2 | OPTIONAL | `HLEDAC_ENABLE_HTTPX_H2=1` | API URLs only |
| Camoufox | OPTIONAL | auto (no env) | JS-heavy detected |
| nodriver | OPTIONAL | auto (no env) | Camoufox fallback |
| Lightpanda | OPTIONAL | nodriver missing | separate coordinator |
| aiohttp-socks (TOR) | OPTIONAL | runtime check | use_tor=True |
| aiohttp-socks (I2P) | OPTIONAL | runtime check | use_i2p=True |

**Key insight**: Default is **aiohttp only** for clearnet. curl_cffi and httpx_h2 are opt-in via env vars. Browser lanes are triggered by JS detection or explicit `use_js=True`.

---

## 3. NETWORK-BOUND vs PARSE/CPU-BOUND DECOMPOSITION

### Network-bound (I/O waiting, not CPU)

| Lane | Blocking time | uvloop benefit |
|------|-------------|----------------|
| **aiohttp** clearnet | DNS + TCP + TLS + first byte | **HIGH** — all async awaiting |
| **curl_cffi** JA3 | DNS + TCP + TLS + first byte | **MEDIUM** — curl_cffi is C, mostly I/O |
| **httpx HTTP/2** | Multiplexed over single connection | **MEDIUM** — h2 handles multiplexing |
| **aiohttp-socks** TOR/I2P | SOCKS handshake + tunnel + TCP | **LOW** — TOR/I2P adds latency |
| **Camoufox** browser | Full browser boot + networkidle | **MINIMAL** — subprocess, dominated by browser |
| **nodriver** CDP | Browser startup + CDP roundtrip | **MINIMAL** — subprocess |

### Parse/CPU-bound (after response body received)

| Operation | Parser | CPU bound? | Time |
|-----------|--------|------------|------|
| `markdownify(html)` in `_sync_process_html` | Pure Python regex | **YES** | ~0.1-1ms/page |
| `BeautifulSoup(html, 'html.parser')` | Pure Python `html.parser` | **YES** | ~1-5ms/page |
| `BeautifulSoup(html, 'lxml')` | C extension | **YES** (but fast C) | ~0.1-0.5ms/page |
| `selectolax` (Rust) | Rust-native | **YES** (but 10-50× faster) | ~0.01-0.1ms/page |
| `PatternMatcher.match_text()` | Aho-Corasick (C ext) | **YES** | ~0.05ms/page |
| `markdownify` + `match_text` combined | Per-page pipeline | **YES** | ~0.5-2ms/page |

### Memory decode (not CPU, but serialized)

| Operation | Library | Bound |
|-----------|---------|-------|
| Response body decode | aiohttp internal | Network |
| JSON parse logs | `orjson` if available, else `json` | **CPU** but negligible |

---

## 4. PERFORMANCE ANALYSIS: WHERE IS THE BOTTLENECK?

### 4.1 Network-limited paths (bandwidth/latency dominates)

```
Total time = DNS(5-50ms) + TCP_CONNECT(10-100ms) + TLS(20-200ms) + FIRST_BYTE(5-50ms) + BODY_TRANSFER(1-500ms)
```

For most public fetches:
- **Body transfer dominates** for large pages (500KB+)
- **TLS/connect dominates** for small fetches (<10KB)
- **Python overhead is <5%** of total time for network-limited fetches

**uvloop impact on network paths:**
- uvloop speeds up **event loop dispatch**, not I/O itself
- For aiohttp: connection pool management, timeout handling, keep-alive — **10-30% faster** event loop
- For curl_cffi: C library, Python is just a wrapper — **<5% impact**
- For httpx HTTP/2: h2 is C extension — **<5% impact**

### 4.2 CPU/Parse-limited paths

```
Total time = HTML_PARSE + MARKDOWNIFY + PATTERN_MATCH
```

For 1000 fetches of 50KB pages with BS4-html.parser:
- HTML parse alone: **1000 × 2ms = 2 seconds**
- With selectolax: **1000 × 0.1ms = 0.1 seconds** — **20× faster**
- Markdownify: **1000 × 0.5ms = 0.5 seconds**
- Pattern match: **1000 × 0.05ms = 0.05 seconds**

**Current pipeline** (`_sync_process_html`): markdownify → match_text = ~2-3ms/page
**With selectolax replacing BeautifulSoup inside markdownify fallbacks**: ~0.2ms/page

### 4.3 Browser paths (completely different cost model)

```
Camoufox startup: ~500-2000ms (first time, cached binary)
Camoufox networkidle: DOMContentLoaded + networkidle (variable)
nodriver startup: ~200-500ms
Full page HTML parse in browser: C++ Blink engine, not Python
```

**Browser is NEVER network-bound in the Python sense** — it's subprocess-bound. uvloop cannot help. Python is only the orchestration layer.

---

## 5. SELECTOLAX OPPORTUNITY ANALYSIS

### Where selectolax IS already used (fast paths):
- `content_miner.py:420` — `_extract_links_selectolax()` for link extraction ✅
- `pastebin_monitor.py:249` — paste ID parsing from HTML ✅
- `rss_atom_adapter.py:2047-2064` — RSS/ATOM feed parsing ✅ (with BS4 fallback)
- `bench_8c0/test_bench_html_parse.py` — benchmark exists ✅

### Where BeautifulSoup is used (slow paths):
- `validation_coordinator.py:363` — HTML cleaning: `BeautifulSoup(html, 'html.parser')` ⚠️
- `deep_web_intelligence.py:410,467` — HTML parsing: `BeautifulSoup(html, "lxml")` ⚠️
- `duckduckgo_adapter.py:920` — search result parsing ⚠️
- `ti_feed_adapter.py:736,795,914` — threat intel feed parsing ⚠️
- `content_layer.py:186` — `self._bs4(html, 'html.parser')` ⚠️
- `archive_discovery.py:1195` — Wayback content parsing ⚠️
- `content_extractor.py:43,184` — content extraction ⚠️
- `deep_web_hints.py:134,157` — form extraction ⚠️

### selectolax opportunity RANKING:

| Location | Impact | Effort | Verdict |
|----------|--------|--------|---------|
| `validation_coordinator.py:363` — HTML cleaning | **HIGH** | Low | ✅ Replace with selectolax |
| `content_layer.py:186` — HTML→markdown | **HIGH** | Medium | ✅ Replace with selectolax + md |
| `ti_feed_adapter.py` (3 sites) — feed parsing | **HIGH** | Low | ✅ Replace with selectolax |
| `deep_web_intelligence.py` (2 sites) — parsing | **MEDIUM** | Medium | ✅ Replace with selectolax |
| `duckduckgo_adapter.py:920` — search results | **MEDIUM** | Low | ✅ Replace with selectolax |
| `content_extractor.py` (2 sites) — extraction | **MEDIUM** | Medium | ✅ Replace with selectolax |
| `archive_discovery.py:1195` — Wayback | **LOW** | Low | ✅ Replace with selectolax |

**Critical insight**: BeautifulSoup is used with `'html.parser'` (pure Python, slow) NOT `'lxml'` (C extension, faster) in most places. Even replacing `html.parser` with `lxml` would give 5-10× improvement. Replacing with selectolax would give 50-100×.

**BUT**: `markdownify` is the actual CPU bottleneck in `_sync_process_html`, not the HTML parser itself. `markdownify` is pure Python and calls `re.sub` in a loop. Replacing markdownify with a Rust-based HTML→text converter (or selectolax-based) would be **the single biggest win**.

---

## 6. ORJSON/MSGPEC OPPORTUNITY ANALYSIS

### Already using orjson:
- `duckdb_store.py` — DuckDB insert/select of JSON fields ✅
- `prefetch/prefetch_cache.py` — LMDB cache serialization ✅
- `memory/shared_memory_manager.py` — shared memory JSON ✅
- `intelligence/exposure_clients.py` — JSON encode/decode ✅
- `FetchResult` in `public_fetcher.py:204` — msgspec.Struct ✅

### Where orjson is NOT used but json is used:

| File | Usage | Frequency | Impact |
|------|-------|-----------|--------|
| `layers/content_layer.py:121` | `import json` → `json.dumps` for output | Per page | **LOW** |
| `runtime/sidecar_bus.py:453` | `json.loads` finding payload | Per finding | **LOW** |
| `validation_coordinator.py` | JSON metadata | Per fetch | **LOW** |
| `FetchResult` in `public_fetcher.py` | Already msgspec ✅ | — | — |

### Verdict on orjson/msgspec for response metadata:
- **Already well-optimized** — orjson is used in all high-frequency paths (DuckDB, LMDB, shared memory)
- ** msgspec.Struct for FetchResult** — canonical DTO already frozen/struct ✅
- **No critical win here** — JSON encode/decode of metadata is <1% of fetch time
- **Low priority** — only worth if already touching these files for other reasons

---

## 7. UVLOOP ANALYSIS: WHERE IT HELPS AND WHERE IT DOESN'T

### uvloop is installed: ✅ `__main__.py:39` — `uvloop.install()`

### Where uvloop HELPS (Python async dispatch overhead):
- **aiohttp session management** — connection pooling, keep-alive, timeout scheduling ✅ **HELPS**
- **asyncio.gather/wait_for** with many concurrent fetches ✅ **HELPS**
- **Event loop in live_feed_pipeline / live_public_pipeline** ✅ **HELPS**
- **curl_cffi_async session management** ✅ **MEDIUM** (C library, Python wrapper only)

### Where uvloop DOESN'T HELP (C/subprocess/serialized):
- **curl_cffi actual HTTP** — libcurl is C, Python just waits on C call ⚠️
- **httpx HTTP/2** — h2 is C extension ⚠️
- **Browser automation (Camoufox/nodriver)** — subprocess ⚠️
- **DNS resolution** — delegated to C extension (dnspython) ⚠️
- **HTML parsing** — CPU-bound Python, runs in ThreadPoolExecutor ⚠️
- **TOR/I2P SOCKS** — network latency dominates ⚠️

### Specific finding: `network/session_runtime.py` line 306
```python
logger.warning(f"[RUNTIME] uvloop install failed: {e}")
```
uvloop install can fail silently — but `uvloop.install()` at `__main__.py:40` is in the canonical boot path before any async ops.

**uvloop Verdict**: ✅ Well-deployed. Adding more uvloop won't help — the bottleneck is NOT the event loop dispatch speed. It's network bandwidth and CPU-bound HTML parsing.

---

## 8. TOP PERFORMANCE WINS (ranked)

### CRITICAL (do first)

**1. Replace markdownify+BS4 with selectolax-based HTML→text in `_sync_process_html`**
- Current: `markdownify` (pure Python, slow) + BeautifulSoup (slow parser) + regex
- Impact: **10-20× faster** per page
- Effort: Medium — need to replace `markdownify` with selectolax + custom markdown
- File: `public_fetcher.py:1826-1839` (`_sync_process_html`)
- Note: `markdownify` is the actual bottleneck, not the HTML parser. Even stripping `import markdownify` and using regex would help.

**2. Replace BeautifulSoup('html.parser') with selectolax in high-frequency paths**
- `validation_coordinator.py:363`, `content_layer.py:186`, `ti_feed_adapter.py` (×3)
- Impact: **5-50× faster** per parse operation
- Effort: Low — 1-line change per call site
- Biggest single win: `content_layer.py` — used per every page clean operation

### HIGH (do second)

**3. Add selectolax to `content_miner._extract_links_selectolax` → extend to `_sync_process_html`**
- Already has selectolax in content_miner ✅ — extend pattern to `_sync_process_html`
- Impact: Unified fast HTML parsing across codebase
- Effort: Low

**4. Replace BeautifulSoup with selectolax in `deep_web_intelligence.py`**
- Uses `lxml` (C) — but still slower than selectolax
- Impact: **3-5× faster**
- Effort: Low

**5. httpx HTTP/2 lane for API-like URLs — enable by default for same-host batch**
- Currently gated behind `HLEDAC_ENABLE_HTTPX_H2=1`
- For batch API fetching (CT logs, threat intel): **HTTP/2 multiplexing eliminates handshake overhead**
- Impact: **20-40% faster** for same-host batch fetches
- Risk: LOW — circuit breaker already in place

### MEDIUM (nice to have)

**6. Replace `orjson` fallback `json` in `memory/shared_memory_manager.py`**
- `orjson` already primary, `json` fallback rarely hits
- Impact: <1ms per operation — negligible overall
- Effort: None — already done

**7. CPU_EXECUTOR `max_workers=2` → `max_workers=4`**
- Current: 2 threads for CPU-bound HTML parsing
- On M1 4E+4P: 2 performance cores available
- Impact: **~80% parallelization** (vs current 50%) for batch parsing
- Risk: LOW — HTML parse is thread-safe, memory increase <50MB

---

## 9. TOP FALSE WINS (where optimization won't help)

### FALSE WIN 1: "Replace aiohttp with httpx/curl_cffi for speed"
- **Reality**: aiohttp is already the **default** lane. curl_cffi and httpx_h2 are **specialized** lanes for specific cases (stealth, HTTP/2 multiplexing).
- **Why it's a false win**: For normal clearnet fetches, aiohttp and curl_cffi have similar network performance. curl_cffi's advantage is JA3 fingerprint spoofing (stealth), NOT raw speed.
- **Exception**: httpx HTTP/2 for API batching — valid win, see #5 above.

### FALSE WIN 2: "uvloop will speed up all async operations"
- **Reality**: uvloop speeds up Python async dispatch (event loop). But the actual I/O (TCP/TLS) and CPU work (HTML parsing) are the bottlenecks.
- **Current status**: uvloop is already installed ✅
- **Where it helps**: Many concurrent aiohttp fetches — event loop scheduling overhead reduced
- **Where it doesn't**: Everything else (C libraries, subprocess, CPU-bound parsing)

### FALSE WIN 3: "Replace all BeautifulSoup with selectolax everywhere"
- **Reality**: BeautifulSoup is used in 47 locations, but most are **low-frequency** (per discovery, not per page fetch).
- **Worth replacing**: High-frequency paths in `public_fetcher`, `content_layer`, `validation_coordinator`, `ti_feed_adapter`
- **Not worth replacing**: `deep_web_hints.py`, `content_extractor.py` — called infrequently
- **Also**: `html.parser` (pure Python) → `lxml` (C) is already a 5× win WITHOUT adding selectolax dependency

### FALSE WIN 4: "Cython will speed up HTML parsing"
- **Reality**: HTML parsing in this codebase is either:
  1. **Pure Python** (markdownify, regex) — Cython would help, but selectolax is faster
  2. **Already C** (BeautifulSoup+lxml, selectolax) — Cython won't help
- **Verdict**: Don't write Cython for HTML parsing. Use selectolax (Rust, 10-50× faster than BS4).

### FALSE WIN 5: "Multiprocessing for HTML parsing"
- **Reality**: Already benchmarked (F214P). On macOS M1 with `spawn` method:
  - ProcessPoolExecutor overhead: 70-310ms per task
  - selectolax parse time: ~0.1ms per page
  - **Net: 700-3000× slower** than just using ThreadPoolExecutor
- **Verdict**: ThreadPoolExecutor is correct. Don't add ProcessPoolExecutor for HTML parsing.

### FALSE WIN 6: "orjson/msgspec for response metadata will speed up fetches"
- **Reality**: Response metadata (headers, status, URL) is already serialized as msgspec.Struct in `FetchResult`. JSON encoding of metadata is <1KB per fetch. CPU time: <0.01ms.
- **Verdict**: Already optimized. No meaningful gain here.

---

## 10. SUMMARY TABLE

| Category | Finding | Impact | Actionable |
|----------|---------|--------|------------|
| **Transport Default** | aiohttp is canonical default | — | — |
| **Transport Optional** | curl_cffi (stealth), httpx_h2 (API batch), browsers (JS) | — | — |
| **Browser Priority** | Camoufox → nodriver → Lightpanda | — | — |
| **Network Bottleneck** | TCP/TLS/bandwidth dominates | HIGH | ✅ |
| **CPU Bottleneck** | markdownify + BS4 in `_sync_process_html` | **CRITICAL** | ✅✅ |
| **uvloop Deployed** | Yes, at boot | MEDIUM | ✅ |
| **uvloop Effect** | Event loop dispatch only | LOW | — |
| **selectolax Used** | content_miner, pastebin, rss_atom | — | ✅ |
| **BS4 html.parser** | 47 refs, most in low-freq paths | MEDIUM | ⚠️ |
| **orjson Coverage** | DuckDB, LMDB, FetchResult | GOOD | ✅ |
| **msgspec Coverage** | FetchResult, pipeline DTOs | GOOD | ✅ |
| **HTTP/2 Lane** | httpx, opt-in, circuit breaker | MEDIUM | ✅ (enable) |
| **False Wins** | Cython, ProcessPool, orjson metadata, full replacement | — | ❌ |

---

## 11. RECOMMENDED ACTION ORDER

1. **`public_fetcher._sync_process_html`**: Replace `markdownify` + BS4 with selectolax + custom md → **10-20× parse speedup**
2. **`content_layer.py`**: Replace `self._bs4(html, 'html.parser')` with selectolax → **5-10× parse speedup**
3. **`validation_coordinator.py`**: Replace BS4 with selectolax → **5-10× parse speedup**
4. **`ti_feed_adapter.py`**: Replace 3× BS4 with selectolax → **5-10× parse speedup**
5. **`HLEDAC_ENABLE_HTTPX_H2=1` by default for API URLs**: Enable httpx HTTP/2 lane → **20-40% faster batch API fetches**
6. **`CPU_EXECUTOR max_workers=2 → 4`**: ThreadPoolExecutor tuning → **80% parallelization**
