# F265C — Speculative Probe Timing + Brotli/0-RTT Analysis

## P1-1: Speculative Probe Timing — Block First Fetch

### Current State (F265B)

```
fetch(url)
  → fetch_via_curl_cffi_cached(url, http_version=None)   # first fetch: no H3
  → probe_altsvc_speculative(url)  # fire-and-forget AFTER fetch
  → _speculative_altsvc_probe_inner → HEAD probe → _cache_put(host, True)
  → next fetch to same host → _altsvc_http_version_for(host) → HttpVersion.v3
```

**Problem:** First fetch to a host always misses H3 because LRU is empty at call time. The probe runs in background AFTER the first fetch completes.

**Root cause in `public_fetcher.py:2557`:**
```python
_curl_result = await fetch_via_curl_cffi_cached(...)  # first fetch: http_version=None
probe_altsvc_speculative(url)  # primes LRU for FUTURE fetches
```

### Blocking Variant — What It Would Require

Change the flow so first fetch BLOCKS until the probe completes:

```python
# Option A: await-based blocking probe
_http_version = await probe_altsvc_blocking(url)  # blocks ~200-400ms
_curl_result = await fetch_via_curl_cffi_cached(url, http_version=_http_version)
```

**API change:** `probe_altsvc_speculative` → `probe_altsvc_blocking` returning `HttpVersion.v3 | None`

**Call sites in `public_fetcher.py` that would need changing:**
- `_fetch_curl_cffi_stealth()` — main curl_cffi path (~3 call sites)
- `_fetch_curl_cffi_403_escalation()` — 403 retry path
- `_fetch_curl_cffi_429_escalation()` — 429 retry path
- Potentially more in the 25+ existing tests

**Risk:** API change at 3+ call sites, regression on 25 passing tests. Reason the issue was deferred.

### Recommended Solution: Internal Pre-Probe in `fetch_via_curl_cffi_cached`

Instead of changing call sites, modify the cached fetch wrapper itself:

```python
async def fetch_via_curl_cffi_cached(..., _pre_probe: bool = False):
    if _pre_probe:
        # Synchronous pre-probe: check LRU first, if miss do blocking HEAD
        host = extract_host(url)
        if _cache_get(host) is None:
            http_version = await _blocking_altsvc_probe(url)  # ~200-400ms
            if http_version:
                _cache_put(host, True)
    # ... rest of function
```

**Advantages:**
- Zero API change at call sites
- `public_fetcher.py` stays unchanged
- Only `curl_cffi_fetch.py` changes
- Fail-soft: if probe fails, falls back to HTTP/1.1/2

**M1 8GB consideration:** Blocking probe adds ~200-400ms to first fetch latency. Acceptable trade-off for H3 speedup on subsequent fetches (~3× on h3-capable servers).

### Alternative: Two-Phase Fetch

Keep current fire-and-forget for SERP/page fetches. Only block for high-value targets (CT data, important pivots) where H3 speedup matters most.

---

## P2: TLS 0-RTT + Brotli

### Brotli — Already Implemented ✓

`transport/decompression.py` — fully working:
- `brotlicffi` (pure CFFI, M1 arm64 wheels)
- `build_accept_encoding_header()` advertises `br` only when importable
- `decode_response_body()` handles `br` decoding with fail-soft
- Layer limit: 3 (prevents pathological multi-layer encodings)
- Body cap: 10MB

**Current behavior:**
```python
# public_fetcher.py:496 — Accept-Encoding header
"Accept-Encoding: gzip, deflate, br (brotli support)"
```

**Verdict:** Brotli works correctly. No changes needed.

### TLS 0-RTT — Not Implemented, Rightly So

**What0-RTT is:**
- TLS 1.3 early data — sends application data before full handshake completes
- Replay risk: attacker can replay0-RTT data
- OSINT context: accepting replayed responses could corrupt data integrity

**curl_cffi support:**
- curl_cffi 0.7+ exposes `CURLOPT_QUIC_TRANSPORT_OPTION` for QUIC
- aioquic (real QUIC lane) supports 0-RTT via `quic.connect(early_data=True)`

**Why correctly deferred:**
1. Security risk without request replay detection
2. curl_cffi 0.7+ required (not in default closure)
3. aioquic resident ~50-80 MB (M1 8GB — already at budget)
4. Marginal latency gain:1-RTT already fast for typical OSINT payloads
5. curl_cffi session reuse provides most of the benefit without0-RTT complexity

**Modern cutting-edge alternative for M1 8GB:**
- **HTTP/3 +0-RTT via aioquic** — only when `[http3]` extra installed
- Real QUIC handshake with0-RTT early data
- Stealth/DA+ profile lane only (high-value targets)
- Fail-soft: falls back to HTTP/2 when0-RTT unavailable

---

## Architecture Recommendations

### P1-1: Pre-Probe Optimization

| Approach | API Change | Risk | M1 Impact | Priority |
|----------|------------|------|-----------|----------|
| Current (fire-and-forget) | None | None | ~0 | Baseline |
| Internal pre-probe in cached fetch | None | Low | +200-400ms first fetch | P1 |
| Blocking call site change | 3+ sites | Medium | +200-400ms first fetch | P2 |
| Two-phase (block for CT only) | 1 site | Low | +200-400ms CT only | P1 |

**Recommended:** Internal pre-probe in `fetch_via_curl_cffi_cached` — zero API change, bounded cost.

### P2: Brotli +0-RTT

| Feature | Status | M1 Safe | Recommendation |
|---------|--------|---------|----------------|
| Brotli | ✓ Working | ✓ | Keep as-is |
| 0-RTT via curl_cffi | Not needed | ✗ | Skip — session reuse sufficient |
| 0-RTT via aioquic | Deferred | ✓ (opt-in) | Keep deferred to `[http3]` extra |

---

## Implementation Plan

### P1-1 Solution: `_pre_probe` parameter in `fetch_via_curl_cffi_cached`

**File:** `transport/curl_cffi_fetch.py`

```python
async def fetch_via_curl_cffi_cached(
    ...,
    _pre_probe: bool = False,  # new param
):
    # Before cache lookup: optional blocking pre-probe
    if _pre_probe and not _force_refresh:
        from .http3_lane import _cache_get, extract_host
        host = extract_host(url)
        if host and _cache_get(host) is None:
            # Blocking probe — ~200-400ms
            _blocking = await _build_blocking_altsvc_probe(url)
            if _blocking:
                _cache_put(host, True)
    # ... rest unchanged
```

**Call site change (only 1):** `public_fetcher.py` — pass `_pre_probe=True` for hosts where H3 matters most (CT, important pivots).

### Test Coverage

```bash
# Existing tests (must not regress)
pytest tests/probe_p14_prewarm_conditional/ -x -q
# New tests: probe_p14b_speculative_blocking/
```

---

## M1 8GB Safety Summary

| Change | RAM Delta | Latency | Risk |
|--------|----------|---------|------|
| Internal pre-probe | ~0 | +200-400ms first fetch | Low |
| Brotli (already on) | 0 | 0 | None |
| aioquic 0-RTT | +50-80 MB | -200ms (handshake) | High (RAM) |

**Final recommendation:** Implement P1-1 internal pre-probe. Keep P2 Brotli as-is. Keep0-RTT deferred to `[http3]` opt-in.
