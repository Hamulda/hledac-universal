# STEALTH_LAYER_WIRING — Sprint Planning Doc

**Status:** Design analysis complete, wiring not yet implemented.
**Sprint scope:** Wire `layers/stealth_layer.py::StealthLayer` and `layers/ghost_layer.py::GhostLayer` into the sprint lifecycle. **`coordination_layer.py` is OUT of scope** (deferred to its own sprint — ~2159L).

---

## 1. File & Line Inventory

| File | Size | Public class | Line | Has DI wiring? |
|------|------|--------------|------|----------------|
| `layers/stealth_layer.py` | 2776 L, 98 KB | `StealthLayer` | L1927 | No (must `inject_*`) |
| `layers/stealth_layer.py` | (same) | `Chameleon` | L2540 | No (used by `StealthLayer`) |
| `layers/ghost_layer.py` | ~1910 L, 30 KB | `GhostLayer` | L800 | No (must `inject_*`) |
| `layers/__init__.py` | ~210 L | `get_stealth_layer()` | L193 | **YES — lazy singleton ready** |
| `layers/__init__.py` | (same) | `get_content_layer()` | L210 | (reference pattern) |
| `layers/__init__.py` | (same) | `get_ghost_layer()` | **MISSING** | Must add |
| `project_types.py` | — | `StealthConfig`, `StealthSession`, `GhostConfig` | L295, L881, ~L500 | YES |
| `fetching/public_fetcher.py` | — | (stealth consumer) | **L2025–2030** | In-place timing jitter |

---

## 2. What `StealthLayer` Actually Adds vs Existing Stack

### 2.1 Existing transport stack (curl_cffi JA3)

`fetching/public_fetcher.py` already provides:
- **JA3 fingerprint spoofing** via `curl_cffi` (`profile="chrome110"`, one-shot escalation on 403/429 at L2053–2059)
- **Tor / I2P / stealth routing** (`use_tor`, `use_i2p`, `use_stealth` flags)
- **Lightpanda pool** (`coordinators/fetch_coordinator.py:_lightpanda_pool`)
- **Cover traffic** (`reset_cover_count` at `runtime/sprint_scheduler.py:7044`)
- **M1 5.5 GB fetch soft ceiling** (`M1_FETCH_SOFT_CEILING_GB` at L2018)

**What is MISSING in the existing stack** (and `StealthLayer` fills it):
| Capability | Existing | `StealthLayer` |
|---|---|---|
| TLS / JA3 fingerprint | ✅ `curl_cffi` chrome110 | ✅ same |
| HTTP/2 ciphers / ALPN | ✅ via curl_cffi | (covered) |
| **Human-like inter-request timing jitter** | ❌ (raw rate-limit) | ✅ `get_timing_jitter()` — Gaussian N(0.5s, 0.3s), clamped [0, 2s] |
| **Browser-level anti-detection** (JS evasion, canvas/WebGL spoof, fingerprint rotation) | ❌ (only Lightpanda) | ✅ `apply_evasion()` (15+ scripts) |
| **CAPTCHA solving** (self-hosted, MLX-accelerated) | ❌ | ✅ `AdvancedCaptchaSolver` (MLX + `microsoft/trocr-small-printed`) |
| **Behavioral simulation** (Bézier mouse curves, scroll) | ❌ | ✅ `BehaviorSimulator` |
| **Process masquerading / anti-debugging** (macOS ptrace) | ❌ | ✅ `Chameleon` (masquerade_process + PT_DENY_ATTACH) |
| **Fingerprint rotation** (UA, fonts, plugins, timezone) | ❌ | ✅ `FingerprintRandomizer` |
| **Per-session browser pool** | ❌ (single Lightpanda pool) | ✅ `create_session()` + `pool_size=2` |
| **M1 Neural Memory Guard** | partial (mlx_cache) | ✅ `force_neural_cleanup()` in GhostLayer |

### 2.2 StealthConfig fields (`project_types.py:295`)

~50 knobs. Most relevant:
```python
enabled: bool = True                  # master switch
timing_jitter: bool = True            # → uses get_timing_jitter()
user_agent_rotation: bool = True
enable_stealth_scripts: bool = True
enable_fingerprint_rotation: bool = True
fingerprint_count: int = 50
enable_canvas_noise: bool = True
enable_webgl_spoofing: bool = True
detection_threshold: float = 0.7
adaptive_mode: bool = True
enable_behavior_simulation: bool = True
enable_captcha_solving: bool = True
captcha_timeout: int = 120
hide_webdriver: bool = True
spoof_chrome_runtime: bool = True
patch_detection_libs: bool = True
randomize_globals: bool = True
session_duration: int = 300           # seconds
platform: str = "macos"
min_delay: float = 0.1
max_delay: float = 0.5
```

**Init surface (L1953–1987):** trivial — only `StealthConfig | None`. No I/O. All components (`_stealth_browser`, `_detection_evader`, `_captcha_solver`, `_js_evasion`, `_chameleon`, `_fingerprint_randomizer`) are **lazy-loaded** in `initialize()`. Browser is on-demand (heavy).

### 2.3 StealthLayer.__init__ requirements
```python
def __init__(self, config: StealthConfig | None = None)
```
- `StealthConfig` is the ONLY required input.
- No env vars, no I/O, no heavy imports on construction.
- `initialize()` is async and fail-soft (returns `False` on any error).

---

## 3. What `GhostLayer` Is (Behavioral Overlay, Not Transport)

`GhostLayer` does **not** wrap the HTTP transport. It is a **behavioral / security overlay** that sits alongside `StealthLayer` with orthogonal responsibilities.

### 3.1 GhostConfig fields (`project_types.py: ~L500`)
```python
max_steps: int = 20                  # anti-loop bound
enable_vault: bool = True            # RamDiskVault for secure storage
vault_size_mb: int = 256
enable_anti_loop: bool = True        # stagnation detection
stagnation_threshold: int = 3        # consecutive same-result threshold
enable_loot_manager: bool = True     # acquired data tracking
```

### 3.2 GhostLayer responsibilities (docstring L805–836)
1. Wraps `GhostDirector` for action execution
2. Manages `RamDiskVault` for secure storage
3. Tracks `LootManager` for acquired data
4. Detects stagnation (infinite loops)
5. Provides **anti-VM protection** via `SystemContext` (`is_vm_environment()`)
6. **M1 Neural Memory Guard** (`force_neural_cleanup()` — gc + MLX clear)

### 3.3 Init surface (L867–888)
```python
def __init__(self, config: GhostConfig | None = None, ghost_director: Any | None = None)
```
- `GhostConfig | None`
- Optional `ghost_director` (for DI from `LayerManager`, prevents duplicate init on M1 8GB)
- `initialize()` is async, fail-soft.

### 3.4 Operational meaning of "ghost mode"
- **NOT a transport path** — same `public_fetcher.py` is used.
- **Behavioral overlay** that:
  - Catches infinite loops (stagnation counter)
  - Detects VM/sandbox environments (anti-forensics)
  - Triggers M1 memory cleanup when pressure rises
  - Tracks acquired data in secure RamDisk vault
  - Activates stealth mode in `SystemContext` (`activate_stealth_mode()` at L890+)

---

## 4. StealthLayer vs GhostLayer — Conflict or Complement?

**Verdict: COMPLEMENTARY. No conflict. Different abstraction layers.**

| Dimension | StealthLayer | GhostLayer |
|----------|--------------|------------|
| Layer | Transport (HTTP) | Behavior (runtime) |
| Targets | External detection (Cloudflare, FingerprintJS) | Internal anti-analysis (VM detection, loop protection) |
| Action | Adds jitter, browser evasions, CAPTCHA solving | Stops infinite loops, cleans memory, masks VM presence |
| Failure mode | WAF/CAPTCHA blocks → solved | Stagnation detected → counter reset + cleanup |
| Init cost | Lazy (browser on demand) | Eager-ish (SystemContext + RamDiskVault) |
| M1 RAM impact | Browser pool: ~150–300 MB | Vault: 256 MB max (bounded) |
| Public singleton | ✅ `get_stealth_layer()` | ❌ must add `get_ghost_layer()` |

**Cross-cutting interaction:** `GhostLayer.activate_stealth_mode()` (L880) can be invoked AFTER `StealthLayer.initialize()` to set a process-level flag that StealthLayer respects via `getattr(self, "_enabled", True)` (L2001).

**Recommended orchestration order:**
1. `GhostLayer.initialize()` (cheap, sets up SystemContext first — if VM detected, abort cleanly)
2. `StealthLayer.initialize()` (loads browser/evasion only if ghost says environment is safe)
3. On each fetch: `StealthLayer.get_timing_jitter()` + optional `GhostLayer.get_system_stats()` telemetry

---

## 5. Exact Integration Seam

### 5.1 The pre-existing seam (TIMING JITTER, already in use)

`fetching/public_fetcher.py:2024–2031` — **this is already wired and working**:
```python
# --- F214Q: Timing jitter — non-blocking, fail-soft ---
if os.environ.get("HLEDAC_ENABLE_STEALTH_LAYER", "0") == "1":
    try:
        from layers import get_stealth_layer
        _sl = get_stealth_layer()
        if _sl:
            await asyncio.sleep(_sl.get_timing_jitter())
    except Exception:
        pass  # fail-soft
```

**Status:** Working in production for `--aggressive` mode. Singleton `get_stealth_layer()` is fail-soft (returns `None` on any error). Latency impact: **0–2 s per request** (Gaussian, mean 0.5 s).

### 5.2 The missing seams (need wiring in THIS sprint)

| Seam | File | Method | Type | Purpose |
|------|------|--------|------|---------|
| **A. Pre-fetch timing** | `fetching/public_fetcher.py:2025` | (done) | pre-fetch middleware | jitter before each fetch — DONE |
| **B. Coordinator injection** | `runtime/sprint_scheduler.py:25404` (after `inject_policy_manager`) | NEW `inject_stealth_layer()` | DI seam | add `_stealth_layer` attr |
| **C. Coordinator injection** | same as B | NEW `inject_ghost_layer()` | DI seam | add `_ghost_layer` attr |
| **D. Initialization** | `core/__main__.py:1425` (where `SprintScheduler(config)` is built) | inline call | bootstrap | call `get_stealth_layer()`/`get_ghost_layer()` + inject |
| **E. CLI flag** | `core/__main__.py:2415` (argparse, near `--aggressive` L2449) | NEW `--stealth-layer` | gate | expose env var to CLI |
| **F. Mode gate** | `core/__main__.py:1316` (run_sprint signature) | extend signature | gate | `stealth_layer: bool = False` |
| **G. Mode gate enforcement** | `core/__main__.py:1415–1422` (where aggressive_mode, extreme_mode flow into config) | inline | gate | only inject if `extreme_mode or args.stealth_layer` |

### 5.3 Recommended wire-up (concrete diff sketch)

**A. `layers/__init__.py`** — add singleton accessor (parallels `get_stealth_layer`):
```python
def get_ghost_layer() -> GhostLayer | None:
    """Lazy singleton GhostLayer accessor. Returns None on init failure (fail-soft)."""
    try:
        from hledac.universal.layers.ghost_layer import GhostLayer
    except Exception:
        return None
    try:
        return GhostLayer()
    except Exception:
        return None
```

**B. `runtime/sprint_scheduler.py`** — after `inject_policy_manager` (L25404):
```python
def inject_stealth_layer(self, stealth: Any) -> None:
    """Inject StealthLayer (F214Q timing jitter + F260 browser evasion)."""
    self._stealth_layer = stealth

def inject_ghost_layer(self, ghost: Any) -> None:
    """Inject GhostLayer (anti-loop + SystemContext + M1 memory guard)."""
    self._ghost_layer = ghost
```

**C. `core/__main__.py` (around L1425, after `SprintScheduler(config)`)**:
```python
scheduler = SprintScheduler(config)

# F260: StealthLayer + GhostLayer (opt-in, EXTREME / --stealth-layer only)
if args.extreme or getattr(args, "stealth_layer", False):
    try:
        from layers import get_stealth_layer, get_ghost_layer
        sl = get_stealth_layer()
        if sl:
            scheduler.inject_stealth_layer(sl)
        gl = get_ghost_layer()
        if gl:
            scheduler.inject_ghost_layer(gl)
    except Exception as e:
        logger.warning(f"[F260] Stealth/Ghost layer injection failed (non-fatal): {e}")
```

**D. `core/__main__.py` argparse (near L2449)**:
```python
parser.add_argument(
    "--stealth-layer",
    action="store_true",
    help="F260: Enable StealthLayer + GhostLayer injection (implies --extreme)",
)
```

### 5.4 Why NOT a transport wrapper (alternative rejected)

The `inject_*` pattern is the **canonical SprintScheduler seam** (12 existing `inject_*` methods: `inject_ioc_graph`, `inject_policy_manager`, `inject_prefetch_oracle`, `inject_pivot_planner`, `inject_analyst_workbench`, `inject_forensics_enricher`, `inject_multimodal_enricher`, `inject_enrichment_services`, `inject_source_economics`, `inject_duckdb_store`, etc.). Wrapping the transport would:
- Break the boundary between `FetchCoordinator` (which already manages 4 backends: aiohttp/curl_cffi/Tor/I2P/Lightpanda) and the singleton layer.
- Add a 5th transport that the team has no operational experience with.
- Make fail-soft + circuit-breaker logic in `FetchCoordinator._domain_failures` (P1-1 already fixed) harder to reason about.

`StealthLayer.get_timing_jitter()` is a **pre-fetch middleware** (already proven at `public_fetcher.py:2025`). The `inject_*` extension makes the same pattern available to `SprintScheduler` for orchestration-level calls (e.g., pivots, deep probe).

---

## 6. Performance Impact Estimate

### 6.1 Per-request overhead

| Component | Cost | Latency overhead | M1 RAM impact |
|-----------|------|------------------|---------------|
| `get_timing_jitter()` | 1× `random.gauss(0.5, 0.3)` | **mean 500 ms, p99 1.4 s, max 2.0 s** | <1 KB |
| `apply_evasion(page)` (Playwright path only) | 15+ `add_init_script` calls | **1.5–3 s one-time per page** | +20–50 MB per page |
| `solve_captcha()` (on demand) | MLX `trocr-small-printed` inference | **200–800 ms per image** | +150 MB transient |
| `force_neural_cleanup()` (Ghost) | `gc.collect()` + MLX eval + clear_cache | **50–200 ms one-shot** | frees 100–500 MB |
| `GhostLayer.initialize()` | SystemContext + vault alloc | **~30 ms one-shot** | 256 MB vault (max) |
| `StealthLayer.initialize()` | browser lazy load | **~0 ms (browser on demand)** | 0 (until used) |

### 6.2 Sprint-level cost (1 sprint, 30 min, ~200 fetches with stealth ON)

- Timing jitter alone: **~100 s added (200 × 0.5 s)**
- 1–2 CAPTCHA solves: **+0.5–1.6 s total**
- 2–3 `force_neepool cleanup` calls: **+0.1–0.6 s**
- 1 `GhostLayer.initialize()`: **30 ms once**

**Total sprint overhead: ~100 s (3% of 1800 s budget) — acceptable.**

### 6.3 M1 RAM budget check

| Component | Steady-state RAM |
|-----------|------------------|
| GhostLayer vault (max 256 MB, lazy alloc) | 0–256 MB |
| StealthLayer browser pool (size=2, headless) | 0 (lazy) → 150–300 MB when active |
| AdvancedCaptchaSolver (MLX trocr) | 0 (lazy) → 150 MB when invoked |
| All combined worst case | **~700 MB** |

**Within M1 8GB UMA budget** (per CLAUDE.md: macOS 2.5 GB + orchestrator 1 GB + LLM 2 GB + KV 0.75 GB + stealth 0.7 GB = **6.95 GB max**, below 8 GB).

### 6.4 Failure-mode cost

- `get_stealth_layer()` raises → `None` returned → public_fetcher skip path → **0 ms added**
- `get_ghost_layer()` raises → `None` returned → SprintScheduler `_ghost_layer is None` check → **0 ms added**
- Browser init fails (e.g., Chrome missing) → `self._stealth_browser = None` (L2067) → browser-dependent code paths raise but are caught by callers → fail-soft ✅

---

## 7. Invariants & Safety Properties

| # | Invariant | Verification |
|---|-----------|--------------|
| 1 | Stealth layer only active in EXTREME / `--stealth-layer` mode | `core/__main__.py:1316` signature + L1415–1422 mode gate |
| 2 | Fail-soft: any `StealthLayer` exception falls back to no-op | `get_stealth_layer()` returns `None` on any init failure (L201–208 in `layers/__init__.py`) |
| 3 | Fail-soft: any `GhostLayer` exception falls back to no-op | NEW `get_ghost_layer()` (same pattern) |
| 4 | `StealthLayer.get_timing_jitter()` is non-blocking, async-safe | Gaussian via `random.gauss` (L2007) — no I/O |
| 5 | No `--disable-gpu` in any browser args (M1 invariant) | `StealthLayer` uses `BrowserConfig` defaults — must NOT add `--disable-gpu` |
| 6 | StealthLayer is the **5th transport tier** — it does NOT replace curl_cffi JA3 | Existing `public_fetcher` transport stack untouched; stealth is overlay |
| 7 | GhostLayer is a behavioral overlay, NOT a transport | Documented in §3 |
| 8 | `SprintScheduler` never breaks if `_stealth_layer` / `_ghost_layer` are `None` | All consumers must check `if self._stealth_layer:` before use |
| 9 | `mx.eval([])` before any `mx.metal.clear_cache()` (M1 invariant) | `GhostLayer.force_neural_cleanup()` path — must follow canonical F183C order |
| 10 | No top-level MLX imports in `core/__main__.py` | `get_ghost_layer()` returns instance; MLX only loaded on `.force_neural_cleanup()` call |

---

## 8. Test Plan (to be implemented in follow-up commit)

| Test ID | Module | Verifies |
|---------|--------|----------|
| `probe_f260_stealth` | NEW | `get_stealth_layer()` returns non-`None` instance with default config |
| `probe_f260_stealth_jitter` | NEW | `get_timing_jitter()` returns float in [0.0, 2.0] |
| `probe_f260_ghost` | NEW | `get_ghost_layer()` returns non-`None` instance |
| `probe_f260_ghost_anti_vm` | NEW | `is_vm_environment()` returns `bool` (does not raise) |
| `probe_f260_inject_none` | NEW | `SprintScheduler.inject_stealth_layer(None)` does not raise |
| `probe_f260_mode_gate` | NEW | Without `--extreme` or `--stealth-layer`, layers are NOT injected |
| `probe_f260_fail_soft` | NEW | Forcing `StealthLayer()` to raise → `get_stealth_layer()` returns `None` → fetch continues |
| `probe_f260_perf` | NEW | Median `get_timing_jitter()` call < 1 ms |

All tests in `tests/test_sprint_f260.py` class `TestSprintF260`. No new public APIs.

---

## 9. Out of Scope (deferred)

- **`coordination_layer.py` (~2159 L)** — its own sprint.
- **Browser-level fetches via `StealthLayer.new_page()`** — only timing jitter is wired today; full Playwright path is for high-value JS-heavy targets (separate sprint).
- **CAPTCHA solving in production** — `enable_captcha_solving=True` requires `2captcha`/`anticaptcha` API keys, not configured.
- **Chameleon process masquerading** — initialized on macOS but only via `StealthLayer._init_chameleon()`. Runtime behavior at sprint-start is best-effort.
- **Tor/I2P integration with `StealthLayer`** — orthogonal (handled by `use_tor`/`use_i2p` flags in `public_fetcher.py`).

---

## 10. Summary

| Question | Answer |
|----------|--------|
| What does `StealthLayer` add vs curl_cffi JA3? | **Timing jitter, browser evasions, fingerprint rotation, behavioral simulation, self-hosted CAPTCHA, M1 Chameleon anti-debug.** |
| Transport wrapper or pre-fetch middleware? | **Pre-fetch middleware (timing) + on-demand browser path.** `inject_*` is the canonical seam. |
| GhostLayer vs StealthLayer? | **Complementary.** Stealth = transport anti-detection. Ghost = runtime anti-analysis + M1 memory guard. |
| Exact integration seam? | `core/__main__.py:1425` (inject) + `runtime/sprint_scheduler.py:25404` (NEW `inject_*` methods) + `fetching/public_fetcher.py:2025` (timing — already done). |
| Performance impact? | **+100 s per 30-min sprint (timing jitter dominates), +700 MB RAM worst case, all fail-soft.** |
| Mode gate? | **EXTREME / `--stealth-layer` only. Default OFF. No toggles in normal mode.** |
