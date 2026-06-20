# Transport Stack Memory Profile — M1 8GB UMA

## 1. Eager Allocations at Import Time

### ✅ WELL-GUARDED (lazy or minimal)
| Component | Status | Evidence |
|-----------|--------|----------|
| `session_runtime.py` | ✅ LAZY | `_session_instance = None`, created on first `async_get_aiohttp_session()` |
| `curl_cffi_runtime.py` | ✅ LAZY | Module-level `_sessions: dict` empty dict (no pre-creation), sessions created per `(host, profile)` lookup |
| `prewarm_pool.py` | ⚠️ 4-SLOT WARM | `_pool: dict = {}`, BUT `acquire_session()` called during `curl_cffi_runtime._get_or_create_session()` → ~60MB resident (4×15MB) if prewarm enabled (default ON) |
| `http3_lane.py` | ✅ LAZY | `aioquic` import wrapped in `_probe_aioquic()`, only on first request; `_aioquic_checked = False` |
| `connection_pool_manager.py` | ✅ LAZY | `TorConnectionPool`/`I2PConnectionPool` singletons created on `get_tor_pool()`/`get_i2p_pool()` first call |
| `transport_resolver.py` | ✅ LAZY | `TransportResolver` classes are bare dict lookups, no I/O at import |
| `conditional_cache.py` | ⚠️ LMDB 16MB | `LMDB_MAP_SIZE = 16 * 1024 * 1024` allocated on `ConditionalCache.__init__()`, but only when LMDB imported |

### ❌ PROBLEMATIC
| Component | Issue | RSS Impact |
|-----------|-------|------------|
| `prewarm_pool._pool` | 4 slots × ~15MB = **60MB** always resident when curl_cffi path active | +60 MB |
| `conditional_cache._lmdb_env` | 16 MB LMDB map allocated immediately on first import even if never used | +16 MB |
| `network.session_runtime._domain_bandits` | `defaultdict(DomainConcurrencyBandit)` — no allocation at import, but grows unbounded per unique host | variable |

---

## 2. Expected RSS by Phase

### Cold Start (no network)
```
Python interpreter:              ~25 MB
hledac core imports:            ~45 MB
transport module globals:         ~5 MB
  └── prewarm_pool dict (empty): <1 MB
  └── curl_cffi_runtime dict:    <1 MB
  └── conditional_cache (if LMDB): 16 MB
  └── uma_budget (no psutil yet):  <1 MB
----------------------------------------
TOTAL COLD:                    ~91 MB
```

### After First HTTP Request (curl_cffi)
```
prewarm pool (4 slots active):  ~60 MB
  └── 4 × AsyncSession @ 15MB each
curl_cffi session pool (per-host):
  └── per active host ~1-3 MB
conditional_cache LMDB:         ~16 MB (if used)
----------------------------------------
TOTAL +first request:           ~167 MB
```

### After Playwright/nodriver Browser Context
```
Browser process (separate):     ~150-400 MB (OFF-PROCESS, not counted in Python RSS)
StealthBrowser._session:         ~15-30 MB (in Python RSS)
----------------------------------------
TOTAL +browser:                 ~182-197 MB (Python RSS only)
```

**NOTE**: Browser runs in separate process (nodriver/CDP). M1 8GB constraint: `HLEDAC_ENABLE_HEAVY_BROWSER=0` by default. Browser is **opt-in only**.

---

## 3. Memory-Pressure Gate Before Browser Launch

### Current State

**`advanced_web/stealth_browser.py`**: NO memory pressure check before `uc.start()`.
```python
# Lines 206-207 — NO guard
browser = await uc.start(
    headless=True,
```

**`HLEDAC_ENABLE_HEAVY_BROWSER=0`** in `.env.example` — browser disabled by default.

**`HLEDAC_BROWSER_MEM_THRESHOLD_GIB=1.5`** defined in `.env.example` but **NOT wired** to `stealth_browser.py`.

### MISSING: Memory Pressure Check in `stealth_browser.py`

The `.env.example` defines `HLEDAC_BROWSER_MEM_THRESHOLD_GIB=1.5` but the check is not implemented.

---

## 4. Env Variables Affecting Memory Footprint

### Memory-relevant vars (checked)

| Variable | Default | 8GB-appropriate? | Issue |
|----------|---------|-------------------|-------|
| `HLEDAC_ENABLE_HEAVY_BROWSER` | `0` | ✅ YES | Browser disabled by default — correct |
| `HLEDAC_BROWSER_MEM_THRESHOLD_GIB` | `1.5` | ⚠️ TOO LOW | Only 1.5GB threshold for browser launch — too permissive for M1 8GB with 6.25GB budget |
| `HLEDAC_MEMORY_SOFT_LIMIT_GIB` | `4.5` | ✅ YES | Within 6.25GB budget |
| `HLEDAC_MEMORY_HARD_LIMIT_GIB` | `6.0` | ✅ YES | Emergency ceiling |
| `HLEDAC_CURL_CFFI_POOL_SIZE` | `4` | ⚠️ INCONSISTENT | .env.example says 4, prewarm_pool.py has `_POOL_SIZE = 4` — consistent but **prewarm is always-on** |
| `HLEDAC_CURL_CFFI_PREWARM` | implied ON | ✅ CORRECT | F265B default ON |
| `HLEDAC_ENABLE_HTTPX_H3` | `0` (legacy) | ✅ CORRECT | HTTP/3 lane opt-in, aioquic costs 50-80MB |
| `HLEDAC_CONDITIONAL_CACHE` | implied ON | ✅ CORRECT | F265B default ON |
| `HLEDAC_HERMES_MIN_UMA_GB` | `2.5` | ✅ CORRECT | MLX needs at least 2.5GB |

### Missing from `.env.example`

| Variable | Should be added? | Reason |
|----------|------------------|--------|
| `HLEDAC_CURL_CFFI_PREWARM` | ✅ YES | Currently only documented in code; operator cannot opt out without code change |
| `HLEDAC_CONDITIONAL_CACHE` | ✅ YES | Same — opt-out documented nowhere |
| `HLEDAC_HTTP3_CACHE_MAX` | ✅ YES | Bounded LRU size (512) should be tunable |
| `HLEDAC_HTTP3_CONCURRENCY_MAX` | ✅ YES | aioquic semaphore cap (3) should be tunable |

---

## 5. Diff-Ready Patches

### Patch 1: Add memory pressure check to `stealth_browser.py`

```python
# BEFORE line 206 in advanced_web/stealth_browser.py
# (after "browser = await uc.start(")

# ADD this guard BEFORE uc.start() call:

# --- Memory pressure gate (M1 8GB) ---
_MEM_CHECK_GIB = float(os.environ.get("HLEDAC_BROWSER_MEM_THRESHOLD_GIB", "1.5"))

proc = psutil.Process()
rss_gib = proc.memory_info().rss / (1024 ** 3)
if rss_gib > _MEM_CHECK_GIB:
    logger.warning(
        f"StealthBrowser: RSS {rss_gib:.2f} GiB > threshold "
        f"{_MEM_CHECK_GIB} GiB, skipping browser launch"
    )
    return None
```

**Requires**: `import psutil` at top (currently not imported in stealth_browser.py).

### Patch 2: Add missing env vars to `.env.example`

```diff
--- a/.env.example
+++ b/.env.example
@@ -12,6 +12,10 @@ HLEDAC_BROWSER_MEM_THRESHOLD_GIB=1.5
 HLEDAC_MEMORY_SOFT_LIMIT_GIB=4.5
 HLEDAC_MEMORY_HARD_LIMIT_GIB=6.0
 HLEDAC_CURL_CFFI_POOL_SIZE=4
+HLEDAC_CURL_CFFI_PREWARM=1        # 0=disable prewarm (saves ~60MB)
+HLEDAC_CONDITIONAL_CACHE=1        # 0=disable ETag/Last-Modified cache
+HLEDAC_HTTP3_CACHE_MAX=512         # Bounded LRU size for Alt-Svc cache
+HLEDAC_HTTP3_CONCURRENCY_MAX=3    # aioquic semaphore cap (saves ~50-80MB)
```

### Patch 3: Lower `HLEDAC_BROWSER_MEM_THRESHOLD_GIB` default for 8GB safety

```diff
- HLEDAC_BROWSER_MEM_THRESHOLD_GIB=1.5
+ HLEDAC_BROWSER_MEM_THRESHOLD_GIB=1.0   # M1 8GB: 1GB leaves room for browser + fetch
```

**Rationale**: At 1.5 GiB threshold, by the time we check, we may already be at 5.5 GiB with 6.25 GiB ceiling. A browser needs 150-400 MB. Setting 1.0 GiB gives more headroom.

### Patch 4: Make prewarm pool size tunable via env

In `transport/prewarm_pool.py`:
```diff
- _POOL_SIZE: int = 4
+ _POOL_SIZE: int = int(os.environ.get("HLEDAC_CURL_CFFI_POOL_SIZE", "4"))
```

Already referenced in `.env.example` but the code does NOT read it — only the constant is used.

---

## 6. `.flags_baseline.json` Consistency Check

**Finding**: `.flags_baseline.json` contains **112 capability flags** (e.g., `MLX_AVAILABLE`, `AIOHTTP_AVAILABLE`). These are **internal module availability markers**, NOT operator-configurable env vars.

| Category | Count | In `.env.example`? |
|----------|-------|-------------------|
| Internal `*_AVAILABLE` capability flags | ~108 | NO (correct — internal only) |
| Actual `HLEDAC_*` env vars | ~23 | PARTIAL (3 missing: `CURL_CFFI_PREWARM`, `CONDITIONAL_CACHE`, `HTTP3_CACHE_MAX`) |

**Verdict**: `.flags_baseline.json` is **NOT stale** — it's a capability registry, not an env-var registry. The drift is expected. No action needed.

---

## 7. RSS Budget Summary (M1 8GB)

```
Budget: 8 GiB total
├── macOS baseline:          ~2.5 GiB
├── Python + hledac core:   ~1.0 GiB
├── prewarm pool (4 slots): ~0.06 GiB  (60 MB)
├── conditional_cache LMDB:   ~0.016 GiB (16 MB)
├── aiohttp session:         ~0.01 GiB  (10 MB)
├── FetchCoordinator:        ~0.05 GiB  (50 MB)
├── Hermes3 MLX (lazy):      ~2.2 GiB   (only when active)
├── KV cache:                ~0.75 GiB  (only when active)
└── Metal cache:             ~1.5 GiB   (max, mlx_lm)
─────────────────────────────────────────
Total allocated (no browser, no MLX): ~3.6 GiB ✅
Total with MLX inference:            ~6.6 GiB ⚠️ (at budget ceiling)
With browser (opt-in):               +0.15-0.4 GiB
```

**Safe for 8GB**: Yes, with defaults. Prewarm + conditional_cache = ~76 MB overhead.

---

## 8. Priority Recommendations

| Priority | Action | Impact |
|----------|--------|--------|
| **P0** | Wire `HLEDAC_BROWSER_MEM_THRESHOLD_GIB` into `stealth_browser.py` | Prevents browser launch when RAM danger |
| **P1** | Lower `HLEDAC_BROWSER_MEM_THRESHOLD_GIB` default from 1.5→1.0 | More headroom for browser on 8GB |
| **P1** | Add `HLEDAC_CURL_CFFI_PREWARM` and `HLEDAC_CONDITIONAL_CACHE` to `.env.example` | Operator opt-out visibility |
| **P2** | Wire `HLEDAC_CURL_CFFI_POOL_SIZE` env var into prewarm_pool `_POOL_SIZE` | Currently ignored in code |
| **P3** | Add `HLEDAC_HTTP3_CACHE_MAX` and `HLEDAC_HTTP3_CONCURRENCY_MAX` to `.env.example` | HTTP/3 tuning visibility |
