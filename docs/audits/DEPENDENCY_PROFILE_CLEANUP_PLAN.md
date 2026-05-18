# Dependency Profile Consistency Cleanup Plan

## Audit Date: 2026-05-18

## Summary of Findings

| Package | Default | osint-html | graph-storage | transport | rerank | Issue |
|---------|---------|------------|---------------|-----------|--------|-------|
| h2 | — | 4.1.0 | — | 4.1.0 | — | DUPLICATE |
| aiohttp-socks | 0.8.0 | — | — | 0.8.0 | — | DUPLICATE |
| lancedb | 0.2.5 | — | 0.2.5 | — | — | DUPLICATE |
| flashrank | 0.2.10 | — | — | — | 0.2.0 | VERSION MISMATCH |
| xxhash | 3.6.0, <4.0.0 | 3.4.0 | — | — | — | BOUND MISMATCH |
| transformers | 5.8.0 | — | — | — | — | M1 8GB CONCERN |

---

## Recommended Actions

### 1. h2 — Remove Duplicate

**Current State:**
- `osint-html`: `h2>=4.1.0`
- `transport`: `h2>=4.1.0`

**Recommendation:** Keep in `transport` only. Add comment in `osint-html` referencing `transport`.

**Rationale:** `h2` is used by the optional `httpx_h2` transport lane, not by `selectolax`/HTML parsing. Moving to `transport` where it's semantically correct.

---

### 2. aiohttp-socks — Remove from Default

**Current State:**
- `default`: `aiohttp-socks>=0.8.0`
- `transport`: `aiohttp-socks>=0.8.0`

**Recommendation:** Remove from `default`, keep only in `transport`.

**Rationale:** `aiohttp-socks` is a transport-layer SOCKS5 dependency. It belongs in `transport` profile, not in default. Default should only contain core production dependencies.

---

### 3. lancedb — Remove from Default

**Current State:**
- `default`: `lancedb>=0.2.5`
- `graph-storage`: `lancedb>=0.2.5`

**Recommendation:** Remove from `default`, keep only in `graph-storage`.

**Rationale:** LanceDB is an optional ANN storage backend for semantic deduplication. It belongs in `graph-storage` profile where it is explicitly used. Default profile should not include it.

---

### 4. flashrank — Unify Version Bound

**Current State:**
- `default`: `flashrank>=0.2.10`
- `rerank`: `flashrank>=0.2.0`

**Recommendation:** Update `rerank` to `>=0.2.10` to match default.

**Rationale:** Using the tighter bound in default is appropriate since it was presumably tested at that version. `rerank` should be consistent.

---

### 5. xxhash — Unify Lower Bound

**Current State:**
- `default`: `xxhash>=3.6.0,<4.0.0`
- `osint-html`: `xxhash>=3.4.0`

**Recommendation:** Update `osint-html` to `>=3.6.0,<4.0.0` to match default.

**Rationale:** The upper bound `<4.0.0` in default should be consistent across all profiles. The lower bound should be unified at `3.6.0`.

---

### 6. transformers — Deferred to Separate HF Audit

**Current State:**
- `default`: `transformers>=5.8.0`

**Recommendation:** Leave as-is. Create separate HF audit for M1 8GB memory implications.

**Rationale:** User explicitly requested this to be handled separately due to M1 8GB memory constraints. This package is heavy and requires careful evaluation before moving.

---

## Proposed pyproject.toml Changes

```diff
diff --git a/pyproject.toml b/pyproject.toml
--- a/default dependencies---
-    "aiohttp-socks>=0.8.0",
     "lancedb>=0.2.5",

--- osint-html ---
     "xxhash>=3.6.0,<4.0.0",        # was >=3.4.0
-    "h2>=4.1.0",

--- rerank ---
     "flashrank>=0.2.10",           # was >=0.2.0
```

---

## Files Affected

- `pyproject.toml`

## Testing After Changes

```bash
pip install -e . --dry-run
pip install -e .[transport] --dry-run
pip install -e .[osint-html] --dry-run
pip install -e .[graph-storage] --dry-run
pip install -e .[rerank] --dry-run
```

---

## Out of Scope

- `transformers` M1 8GB audit (separate task)
- Any other dependency changes
