# M1 8GB Memory Pressure Audit

**Date:** 2026-05-18
**Scope:** `hledac/universal/` — analysis-only, no implementation
**Hardware target:** MacBook Air M1 8GB UMA (<5.5GB active budget)

---

## Summary

| Category | Count |
|----------|-------|
| High-risk unbounded sites | 1 |
| Medium-risk sites | 2 |
| Already-safe bounded sites | 9 |
| False positives corrected | 2 |

---

## Top 10 Memory Risk Sites

### 1. `transport/httpx_transport.py` — Body cap declared but never enforced

| Field | Value |
|-------|-------|
| **Risk** | HIGH — httpx transport lane has no body cap; large responses load fully into memory |
| **Bound exists?** | NO enforcement — `_max_bytes=2MB` declared but marked `# noqa: F841`, never applied |
| **Evidence** | `body_limiter.read_body_with_cap` is NOT imported or called in httpx transport. `.get()` returns `httpx.Response` — caller must read body; no cap applied. curl_cffi lane uses `read_body_with_cap` with `MAX_BYTES=10MB` ✅. |
| **M1 impact** | A single 50MB response on httpx lane fills ~10% of available RAM |
| **Suggested measurement** | Monitor `psutil.Process().memory_info().rss` during httpx lane fetches |

**Verdict:** httpx transport is the body-cap gap — curl_cffi lane is safe, httpx lane is not.

---

### 2. `knowledge/semantic_store.py` — `_MAX_PENDING` IS present and enforced

| Field | Value |
|-------|-------|
| **Risk** | LOW — buffer IS bounded via `_MAX_PENDING = 10_000` and `popleft()` drop-oldest on overflow |
| **Bound exists?** | YES — `_MAX_PENDING = 10_000` defined; `buffer_finding` checks `len >= _MAX_PENDING` and drops oldest |
| **Evidence** | `semantic_store.py` lines: `_MAX_PENDING = 10_000  # Bounded pending buffer (M1 8GB safety)`. `buffer_finding` has `if len(self._pending_texts) >= _MAX_PENDING: ... popleft()`. |
| **M1 impact** | 10,000 texts × 512 chars max each ≈ 5MB ceiling — within budget |
| **Verdict** | ✅ Already safe — initial read was against wrong file (`semantic_store_buffer.py` is a thin delegation wrapper; actual deque lives in `semantic_store.py` with proper bounds) |

---

### 3. `runtime/graph_accumulator.py` — `rows` list accumulated unbounded

| Field | Value |
|-------|-------|
| **Risk** | MEDIUM — single `rows.append((fid, src_type, confidence, sprint_id))` loop without size gate |
| **Bound exists?** | NO explicit bound on `rows` list |
| **Evidence** | `rows.append(...)` called once per accepted finding, then passed to `gs.upsert_ioc_batch(rows)`. No bound on how many findings accumulate before the call. |
| **M1 impact** | In a 5000-finding sprint with 100% acceptance, `rows` list grows to 5000 × ~100 bytes ≈ 0.5MB — low risk alone, but combined with other accumulators |
| **Suggested measurement** | Track `len(rows)` at point of `upsert_ioc_batch` call via `len(rows)` logging |

**Verdict:** Low immediate risk but violates bounded-collection invariant. Add `MAX_GRAPH_BATCH` (e.g., 1000) and chunk the call.

---

### 4. `knowledge/duckdb_store.py` — `_pending_upserts` does NOT exist

| Field | Value |
|-------|-------|
| **Risk** | FALSE POSITIVE |
| **Evidence** | Codebase search confirms `_pending_upserts` does not exist in current `duckdb_store.py`. Prior sprint audit incorrectly flagged it. |
| **Current batch safety** | `async_record_shadow_findings_batch(..., max_batch_size=500)` — chunked at 500 |
| **Verdict** | ✅ Already safe |

---

## Top 10 Already-Safe Bounded Sites

| # | File | Pattern | Bound | Notes |
|---|------|---------|-------|-------|
| 1 | `runtime/sprint_scheduler.py` | `asyncio.PriorityQueue(maxsize=200)` | 200 | `_pivot_queue` — properly bounded |
| 2 | `knowledge/lancedb_store.py` | `_HLEDAC_HARD_MAX_CACHE_MB = 512` | 512MB | Cache hard-capped for M1 8GB |
| 3 | `knowledge/duckdb_store.py` | `async_record_shadow_findings_batch(max_batch_size=500)` | 500 | Chunked batch ingestion |
| 4 | `knowledge/lancedb_store.py` | `_embed_batch(..., batch_size=16)` | 16 | MLX embedding batch size |
| 5 | `knowledge/duckdb_store.py` | `REPLAY_CHUNK_SIZE = 100` | 100 | WAL replay chunk |
| 6 | `knowledge/duckdb_store.py` | `MAX_RETRY_COUNT = 3` | 3 | Dead-letter retry cap |
| 7 | `knowledge/duckdb_store.py` | `_DUCKDB_MAX_TEMP = "1GB"` | 1GB | Temp directory size |
| 8 | `knowledge/semantic_store.py` | `_MAX_PENDING = 10_000` | 10,000 | Pending buffer cap with popleft() drop-oldest |
| 9 | `transport/curl_cffi_fetch.py` | `MAX_BYTES = 10 * 1024 * 1024` | 10MB | Body cap on curl_cffi lane via `read_body_with_cap` |

---

## Detailed Findings by Category

### A. Fetch / HTTP Body

**`transport/curl_cffi_fetch.py`**
- `MAX_BYTES = 10 * 1024 * 1024` (10MB hard cap) ✅
- `read_body_with_cap(chunks, max_bytes)` uses `bytearray.extend()` O(1) amortized — safe
- `body_limiter.read_body_with_cap` IS called in curl_cffi fetch path ✅
- **Risk:** LOW — body cap enforced

**`transport/httpx_transport.py`**
- `_max_bytes=2MB` declared but `# noqa: F841` — dead code, never applied
- `body_limiter.read_body_with_cap` NOT imported or called ❌
- `.get()` returns `httpx.Response` — caller reads full body with no cap
- **Risk:** HIGH — body cap missing on httpx lane

**`fetching/public_fetcher.py`**
- `FetchResult` has `text: str | None` (NOT `raw_html`) — already a text field, not raw bytes
- `size_cap_exceeded` is a string constant for error reporting, not a cap mechanism
- No body cap on `text` field — relies on transport layer cap
- **Risk:** MEDIUM (depends on httpx lane usage)

### B. Text / Payload

**`pipeline/live_public_pipeline.py`**
- `payload_text` hard-capped at 2000 chars ✅
  ```python
  if len(payload_text) > 2000:
      payload_text = payload_text[:2000]
  ```
- **Risk:** LOW — already safe

**`fetching/public_fetcher.py` — `FetchResult`**
- `text: str | None` — the extracted response body (not raw bytes). No explicit size cap; relies on transport layer enforcement.
- `declared_length: int` — tracked from `Content-Length` header, not enforced as cap
- `body_read_error: bool` — set true when body stream failed mid-read
- **Risk:** MEDIUM (depends on which transport lane is used; curl_cffi is safe, httpx is not)

### C. Finding Batches

**`knowledge/duckdb_store.py`**
- `async_record_shadow_findings_batch(..., max_batch_size=500)` ✅ — chunked
- WAL replay: `REPLAY_CHUNK_SIZE = 100` ✅
- **Risk:** LOW — already safe

**`runtime/graph_accumulator.py`**
- `rows.append((fid, src_type, confidence, sprint_id or ""))` — no explicit bound
- Called once per accepted finding before `upsert_ioc_batch(rows)`
- **Risk:** MEDIUM — needs `MAX_GRAPH_BATCH` bound

### D. Semantic Buffer

**`knowledge/semantic_store.py` — `SemanticStore`**
- `_MAX_PENDING = 10_000` ✅ — hard bound on deque
- `buffer_finding`: if `len >= _MAX_PENDING`: `popleft()` oldest then append ✅
- `deque` maxlen not set (uses explicit size check — same effect) ✅
- **Risk:** LOW — already safe

**`knowledge/lancedb_store.py`**
- `_HLEDAC_HARD_MAX_CACHE_MB = 512` ✅ — hard cap
- `_embed_batch(..., batch_size=16)` ✅ — MLX-safe batch
- **Risk:** LOW — already safe

### E. Queues / Concurrency

**`runtime/sprint_scheduler.py`**
- `_pivot_queue: asyncio.PriorityQueue(maxsize=200)` ✅ — properly bounded
- `asyncio.QueueFull` / `QueueEmpty` handling present at lines 10701, 10756, 10896, 10898
- **Risk:** LOW — already safe

**`runtime/sidecar_bus.py`, `runtime/sidecar_dispatcher.py`**
- No `Queue(...)` definitions found — use synchronous lists or are not yet implemented
- **Risk:** LOW — no queue pressure

### F. LMDB / DuckDB Buffers

**`knowledge/duckdb_store.py`**
- `_DUCKDB_MAX_TEMP = "1GB"` ✅ — temp dir bounded
- `_wal_lmdb` uses standard LMDB with `map_size` set from env
- **Risk:** LOW — already safe

### G. Model / MLX Memory

**`brain/` (Hermes3Engine, mlx_lm)**
- `mx.eval([])` barrier before `clear_cache()` confirmed ✅
- `set_cache_limit(0)` called only after model swap ✅
- `MAX_BATCH=32` for M1 8GB safety in `mlx_lm.generate()` ✅
- **Risk:** LOW — already compliant with GHOST_INVARIANTS

### H. Browser / Vision

**`rendering/macos_webkit_renderer.py`**
- WebKit process spawned per render; no `max_children` or pool bound found in grep
- **Risk:** MEDIUM — process pool could grow under high concurrency
- Recommended: `max_concurrent_renders` bound (e.g., 2 for M1)

**`multimodal/analyzer.py` — `MultimodalEnricher`**
- RAM guard at >85% UMA pressure ✅ — `is_warn/is_critical/is_emergency`
- **Risk:** LOW — already guarded

---

## Proposed Benchmark Additions

| Test | File | What to measure |
|------|------|-----------------|
| `test_httpx_body_cap_enforced` | `tests/probe_f226_di_seams.py` | httpx lane fetches 10 × 5MB responses — `text` field should be capped at `_max_bytes` |
| `test_semantic_store_pending_cap` | `tests/test_sprint47.py` | After 15,000 findings, pending buffer size — should be ≤ 10,000 or 0 after flush |
| `test_graph_accumulator_batch_bound` | `tests/test_sprint6d.py` | `len(rows)` at `upsert_ioc_batch` call — should be ≤ `MAX_GRAPH_BATCH` |
| `test_duckdb_chunked_batch` | `tests/test_sprint8ax_duckdb_shadow.py` | Verify `async_record_shadow_findings_batch(1001 findings)` produces exactly 3 calls (500+500+1) |
| `test_lancedb_cache_hard_cap` | `tests/test_sprint6e.py` | After embedding 10k texts, `lancedb_cache_mb` — should be ≤ 512MB |
| `test_curl_cffi_body_cap` | `tests/probe_f226_di_seams.py` | curl_cffi lane: response >10MB — verify `text` is truncated and `body_read_error` is set |

---

## Recommended Priority Fixes (Analysis Only)

| Priority | File | Issue | Suggested fix |
|----------|------|-------|---------------|
| P0 | `httpx_transport.py` | No body cap on httpx lane | Wire `read_body_with_cap` into httpx fetch path, or apply `_max_bytes` limit |
| P1 | `graph_accumulator.py` | No bound on `rows` list | Add `MAX_GRAPH_BATCH = 1000`; chunk `upsert_ioc_batch` |
| P1 | `macos_webkit_renderer.py` | No render process pool bound | Add `max_concurrent_renders = 2` for M1 |
| P2 | `fetching/public_fetcher.py` | `text` field has no explicit cap | Add `MAX_FETCH_TEXT = 2 * 1024 * 1024` truncation guard |

> **Note:** `semantic_store.py` and `duckdb_store.py` findings were false positives — both are already properly bounded.

---

## M1 RAM Budget Reference

| Component | Budget |
|-----------|--------|
| macOS baseline | ~2.5GB |
| Orchestrátor | ~1GB |
| LLM (Hermes-3 4bit) | ~2GB |
| KV cache | ~0.75GB |
| **Headroom** | **<0.25GB** |

Any single unbounded allocation >100MB is a potential OOM trigger under sustained load.