# Local OSINT Capability Matrix — MacBook Air M1 8GB

> **Goal:** Document exact runtime capability modes and their memory/extras requirements for M1 Air 8GB.

## Quick Reference

| Profile | Extras | MLX | Browser | OCR | Torch | M1 Safe |
|---------|--------|-----|---------|-----|-------|----------|
| `default` | — | — | — | — | — | ✅ |
| `m1-local` | `apple-accel,osint-html,graph-storage,acceleration,transport` | ✅ | — | — | — | ✅ |
| `m1-local+dev` | `m1-local` + `dev` (two extras) | ✅ | — | — | — | ✅ |
| `osint-html` | `selectolax,curl_cffi,h2` | — | — | — | — | ✅ |
| `graph-storage` | `duckdb,lancedb,pyarrow,polars` | — | — | — | — | ✅ |
| `graph-truth` | `kuzu` | — | — | — | — | ⚠️ source |
| `browser` | `camoufox,nodriver` | — | ✅ | — | — | ⚠️ RAM |
| `webkit` | `pyobjc-webkit` | — | ✅ (light) | — | — | ✅ |
| `ocr` | `pytesseract` | — | — | ✅ | — | ✅ |
| `torch` | `torch,torchvision` | — | — | — | ✅ | ⚠️ heavy |
| `rerank` | `flashrank` | — | — | — | — | ✅ |
| `light` | `fast-langdetect,datasketch` | — | — | — | — | ✅ |
| `nlp` | `fast-langdetect` | — | — | — | — | ✅ |
| `security` | `cryptography>=48.0.0` | — | — | — | — | ✅ |
| `search` | `duckduckgo-search>=8.0.0` | — | — | — | — | ✅ |
| `legacy-html` | `beautifulsoup4>=4.12.0` | — | — | — | — | ✅ |
| `coreml` | `coremltools,pyobjc-framework-coreml` | — | — | — | — | ⚠️ darwin |
| `all` | everything except `torch` | ⚠️ | ⚠️ | ✅ | ⚠️ | ⚠️ |

---

## 1. Core CLI

Minimal install for headless OSINT CLI.

```bash
uv sync
# or
uv sync --extra dev
```

| Field | Value |
|-------|-------|
| **deps** | `default` (transformers,duckdb,lancedb,aiohttp,httpx)` |
| **expected memory** | ~2 GB (base install; MLX/browsers add significant overhead) |
| **network** | optional (httpx, aiohttp-socks) |
| **MLX allowed** | ❌ |
| **browser** | ❌ |
| **OCR** | ❌ |
| **torch** | ❌ |

---

## 2. M1 Local Research

Recommended ergonomic profile for Apple Silicon MacBook Air M1 8GB.

```bash
uv sync --extra m1-local --extra dev
```

| Field | Value |
|-------|-------|
| **deps** | `m1-local` → `apple-accel,osint-html,graph-storage,acceleration,transport` |
| **expected memory** | ~3.5 GB (with MLX model loaded) |
| **network** | ✅ curl_cffi stealth + httpx H2 + SOCKS5 |
| **MLX allowed** | ✅ Hermes-3-Llama-3.2-3B-4bit |
| **browser** | ❌ |
| **OCR** | ❌ |
| **torch** | ❌ |

**Notes:**
- MLX uses unified memory (GPU=CPU); kv_bits=4, max_kv_size=8192 in `mlx_lm.generate()`
- RAM budget: macOS ~2.5GB + orchestrator ~1GB + LLM ~2GB + KV cache ~0.75GB = ~6.25GB max
- No parallel model inference; no browser; no OCR

---

## 3. Fast HTML/Crawling

Fast HTML parsing + OSINT-specific HTTP transport.

```bash
uv sync --extra osint-html
```

| Field | Value |
|-------|-------|
| **deps** | `selectolax,curl_cffi,h2,xxhash` |
| **expected memory** | ~2 GB |
| **network** | ✅ curl_cffi (JA3) + HTTP/2 |
| **MLX allowed** | ❌ |
| **browser** | ❌ |
| **OCR** | ❌ |
| **torch** | ❌ |

**Notes:**
- `selectolax` is 10-50× faster than BeautifulSoup4
- `curl_cffi` provides JA3 TLS fingerprint impersonation
- Fallback chain: `selectolax → bs4+lxml → regex`

---

## 4. Graph Storage

Columnar storage + DuckDB + LanceDB for analytics and ANN dedup.

```bash
uv sync --extra graph-storage
```

| Field | Value |
|-------|-------|
| **deps** | `duckdb,lancedb,pyarrow,polars` |
| **expected memory** | ~2.5 GB |
| **network** | — |
| **MLX allowed** | ❌ |
| **browser** | ❌ |
| **OCR** | ❌ |
| **torch** | ❌ |

**Notes:**
- DuckDB is canonical analytics backend (DuckPGQGraph)
- LanceDB is ANN fast path for semantic dedup
- `duckdb-store` is always available in default/m1-local

---

## 5. Kuzu/IOC Graph Truth

Optional truth store for entity relationships (not default).

```bash
uv sync --extra graph-truth
```

| Field | Value |
|-------|-------|
| **deps** | `kuzu>=0.6.0` |
| **expected memory** | ~3 GB |
| **network** | — |
| **MLX allowed** | ❌ |
| **browser** | ❌ |
| **OCR** | ❌ |
| **torch** | ❌ |

**⚠️ Warning:** Kuzu has no cp314 arm64 wheel — install from source.

**Notes:**
- `IOCGraph` (Kuzu) is the truth store
- `DuckPGQGraph` (DuckDB) is canonical analytics backend and always available
- Default/m1-local: NO kuzu dependency

---

## 6. Browser Rendering

Full JavaScript rendering via camoufox (bundled Chrome) or nodriver fallback.

```bash
uv sync --extra browser
```

| Field | Value |
|-------|-------|
| **deps** | `camoufox[geoip],nodriver` |
| **expected memory** | ~5 GB+ |
| **network** | ✅ |
| **MLX allowed** | ⚠️ disabled during rendering |
| **browser** | ✅ camoufox primary, nodriver fallback |
| **OCR** | ❌ |
| **torch** | ❌ |

**⚠️ Memory Warning:** Browser binary alone consumes ~2GB RAM. Rendering with MLX model loaded exceeds 8GB UMA ceiling. Use `--disable-gpu` **never on M1** (GPU=CPU on UMA, slows to a crawl).

**Notes:**
- camoufox provides JA3 fingerprint + bundled browser binary
- `nodriver` is lazy-import fallback (fail-soft)
- M1: run browser **OR** MLX, never both simultaneously

---

## 7. OCR

Optical character recognition for captcha solving.

```bash
uv sync --extra ocr
```

| Field | Value |
|-------|-------|
| **deps** | `pytesseract>=0.3.10` |
| **expected memory** | ~2 GB |
| **network** | — |
| **MLX allowed** | ❌ |
| **browser** | ❌ |
| **OCR** | ✅ pytesseract |
| **torch** | ❌ |

**Notes:**
- Lazy import with `try/except ImportError` — fail-soft by design
- Primary OCR path uses vision pipeline (MultimodalEnricher)
- `pytesseract` is a fallback for captcha_solver.py

---

## 8. Tor/Stealth

Tor control + SOCKS5 stealth transport.

```bash
uv sync --extra tor --extra transport
```

| Field | Value |
|-------|-------|
| **deps** | `stem>=1.8.0,h2,aiohttp-socks` |
| **expected memory** | ~2 GB |
| **network** | ✅ Tor + SOCKS5 |
| **MLX allowed** | ❌ (Tor is slow; model should not share memory) |
| **browser** | ❌ |
| **OCR** | ❌ |
| **torch** | ❌ |

**Notes:**
- `stem` lazy import, fail-soft if Tor not running
- Stealth transport uses curl_cffi for JA3 fingerprint
- `aiohttp-socks` for SOCKS5 proxy support

---

## 9. Heavy ML/HF

HuggingFace transformers + neural reranking.

```bash
uv sync --extra rerank --extra dev
```

| Field | Value |
|-------|-------|
| **deps** | `flashrank>=0.2.0` (transformers already in default deps) |
| **expected memory** | ~4 GB+ (HF models exceed M1 8GB ceiling) |
| **network** | optional |
| **MLX allowed** | ❌ (HF and MLX both need UMA) |
| **browser** | ❌ |
| **OCR** | ❌ |
| **torch** | ⚠️ if flashrank uses torch backend |

**⚠️ Memory Warning:** Heavy ML on M1 8GB exceeds UMA ceiling. Use rerank-only with MLX off.

---

## 10. Torch Fallback

PyTorch (CPU or MPS) — not recommended as default on M1 8GB.

```bash
# CPU-only (recommended for cross-platform)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# Or with MPS (Apple GPU)
pip install torch torchvision
```

| Field | Value |
|-------|-------|
| **deps** | `torch>=2.1.0,torchvision>=0.16.0` |
| **expected memory** | ~4 GB+ |
| **network** | — |
| **MLX allowed** | ❌ |
| **browser** | ❌ |
| **OCR** | ❌ |
| **torch** | ✅ |

**⚠️ Warning:** Torch is **never in default deps**. Installed separately due to size. Avoid on M1 8GB for routine dev — use `m1-local` instead.

---

## Profile Comparison

| Profile | Memory | MLX | Browser | Stealth | Graph | Torch | Best For |
|---------|--------|-----|---------|---------|-------|-------|----------|
| `default` | ~2GB | ❌ | ❌ | partial | duckdb+lancedb | ❌ | CI, minimal env |
| `m1-local` | ~3.5GB | ✅ | ❌ | ✅ | full | ❌ | Daily local research |
| `osint-html` | ~2GB | ❌ | ❌ | ✅ | partial | ❌ | Fast crawling |
| `graph-storage` | ~2.5GB | ❌ | ❌ | ❌ | full | ❌ | Analytics |
| `graph-truth` | ~3GB | ❌ | ❌ | ❌ | full | ❌ | Entity truth store |
| `browser` | ~5GB+ | ⚠️ | ✅ | ✅ | partial | ❌ | JS-rendered sites |
| `webkit` | ~3GB | ❌ | ✅ light | ❌ | partial | ❌ | macOS-only rendering |
| `ocr` | ~2GB | ❌ | ❌ | ❌ | ❌ | ❌ | Captcha solving |
| `rerank` | ~5GB+ | ❌ | ❌ | ❌ | ❌ | ⚠️ | Neural reranking |
| `torch` | ~4GB+ | ❌ | ❌ | ❌ | ❌ | ✅ | Cross-platform ML |

---

## M1 8GB Hard Constraints

| Constraint | Value | Consequence |
|------------|-------|-------------|
| UMA ceiling | 8 GB | MLX + Browser = OOM |
| MLX model | ~2 GB | Max kv_size=8192, bits=4 |
| KV cache | ~0.75 GB | Bounded, not streaming |
| macOS base | ~2.5 GB | Non-negotiable |
| Orchestrator | ~1 GB | Sprint overhead |

**Rule:** Run MLX **OR** Browser, never both.
**Rule:** Never add `--disable-gpu` on M1 — GPU=CPU on UMA, slows to a crawl.
**Rule:** `mx.eval([])` before `mx.metal.clear_cache()` — always.
