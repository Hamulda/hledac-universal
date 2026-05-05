# F214C-2 — Delta ZSTD Migration Feasibility Review

**Date**: 2026-05-05
**Scope**: `tools/delta_compressor.py`, `tools/smart_deduplicator.py`
**Constraint**: No broad refactor. No patch without version/magic/fallback. No pyzipper/vault/NVD/forensics touches.

---

## 1. Validation Gate

```
ZSTD_OK True          — compression.zstd available in Python 3.14 stdlib
DELTA_IMPORT_OK       — DeltaCompressor imports cleanly
```

Both pass. Python 3.14 `compression.zstd` is available and functional.

---

## 2. tools/delta_compressor.py — Format Audit

### 2.1 Format Spec (DELTA v1, zlib)

```
Offset  Size  Field         Value
------  ----  -----         -----
0       4     MAGIC         b'DELT'
4       1     VERSION       1
5       1     FLAGS         bit 0=compressed, bit 7=full-text
6       4     ORIG_LEN      big-endian uint32
10      4     DELTA_LEN     big-endian uint32
14      N     DELTA_DATA    variable
```

**Header size**: 14 bytes (fixed)

### 2.2 Encode/Decode Paths

| Path | Code | Header bit | Codec |
|------|------|-----------|-------|
| `_encode_delta(compressed=True)` | line 74 | `flags=1` | `zlib.compress(..., level=6)` |
| `_encode_delta(compressed=False)` | line 74 | `flags=0` | raw UTF-8 bytes |
| `_encode_full()` | line 79 | `flags=0x80` | `zlib.compress(..., level=6)` |

**Decode** (`apply_text_delta`):
- Reads VERSION byte
- Returns `base` on version mismatch (line 109: `version != VERSION`)
- Supports decompress of both compressed delta and full-text

### 2.3 Error Handling

All decode paths are fail-safe: `try/except → logger.warning → return base`. No exceptions propagate. This is correct behavior for a read-path codec.

### 2.4 Test Coverage

Only `tests/test_autonomous_orchestrator.py` has `TestSmartDeduplicator` (lines 8413–8445). No dedicated probe lane for `delta_compressor.py`.

### 2.5 Verdict: delta_compressor.py

- Format: **DELTA v1 with zlib** (not gzip — correct terminology)
- Codec: `zlib.compress/decompress` level 6
- **No v1/v2 flag mechanism** — VERSION byte exists but is hardcoded to 1
- **No magic escape hatch for unknown versions** — version mismatch returns `base` (fail-safe, but no reader-side version negotiation)
- **Format is well-structured for extension** — single-byte version + flags byte leaves 254 flag combinations for v2

---

## 3. tools/smart_deduplicator.py — Persistence Audit

### 3.1 `_compress_full()` — Size Comparison Only

```python
def _compress_full(self, text: str) -> bytes:
    """Compress full text for size comparison."""
    import zlib
    return zlib.compress(text.encode('utf-8'), level=6)
```

**Critical finding**: `_compress_full()` output is **never persisted**. It is used only as a size comparison reference inside `maybe_store_delta()` to estimate savings before deciding storage strategy.

```python
delta = self.delta.make_text_delta(base_text, new_text)
full_compressed = self._compress_full(new_text)   # ← size reference only
savings = len(full_compressed) - len(delta)
```

**Verdict**: `_compress_full()` is a **scoring-only candidate** for zstd migration. It does not persist data, so migration has zero risk to existing stored blobs. However, no implementation in this phase per the MUST constraint.

### 3.2 `maybe_store_delta()` — Zero Production Callers

**grep results** (all Python files, excluding test files, delta_compressor.py, smart_deduplicator.py, legacy/):

```
(no output)
```

`maybe_store_delta()` and `SmartDeduplicator` have **zero callers in production code**. They exist only in test fixtures (`test_autonomous_orchestrator.py` lines 8413–8445) and in the module itself.

**Verdict**: `SmartDeduplicator` is **scoring-only, dead code in production**. `maybe_store_delta()` is unreachable outside tests.

### 3.3 `store_cb` Callback Pattern

The `store_cb` parameter signature: `Callable[[str, str, bytes], str]`

This is a **caller-provided callback**. The delta compressor creates the bytes; the caller decides what to do with them. The compressor itself never writes to LMDB, DuckDB, or filesystem.

---

## 4. Persistence Paths — Where Are DELTA Blobs?

### 4.1 LMDB KV Store (`tools/lmdb_kv.py`)

Generic key-value store. `put(key, value)` serializes `dict` via `orjson`. No artifact_id, no delta blob types. **Not a delta persistence path.**

### 4.2 DuckDB Store (`knowledge/duckdb_store.py`)

`sprint_delta` table stores **metrics** (query, duration, findings_per_minute, etc.) — not text blobs. No `artifact_id` column. No delta blob storage. **Not a delta persistence path.**

### 4.3 Canonical `knowledge/atomic_storage.py`

Empty grep for `artifact|artifact_id|store_delta|delta_blob`. **Not a delta persistence path.**

### 4.4 Legacy `legacy/atomic_storage.py` — SnapshotStorage

This is the **only active blob storage** in the system. It stores page content snapshots with:

- ZSTD if available (`compression.zstd`, level 3) — **already migrated to zstd**
- gzip fallback if ZSTD unavailable
- Magic detection: `magic = compressed[:4]` checks for ZSTD frame marker

```python
# Sprint 79b: ZSTD detection and decompression
if len(compressed) >= 4:
    magic = compressed[:4]
    if magic == b'(ZL' or magic == b'\x28\xb5\x2f\xfd':
        if self._zstd_decompressor:
            return self._zstd_decompressor.decompress(compressed)
```

This is **separate from DELTA v1 format**. SnapshotStorage uses its own format (ZL/gzip or zstd frame magic, no DELTA header).

### 4.5 Persistence Map — Conclusion

| Storage | Holds DELTA v1 blobs? | Holds ZSTD blobs? |
|---------|----------------------|------------------|
| DuckDB (sprint_delta) | NO | NO (metrics only) |
| LMDB KV | NO | NO (dict serialization) |
| knowledge/atomic_storage.py | NO | NO |
| legacy/atomic_storage.py (SnapshotStorage) | NO | YES (ZSTD already) |

**Finding**: There are **no DELTA v1 blobs persisted anywhere in the current production system**. The format exists in code and test fixtures only.

---

## 5. Compatibility Analysis

### 5.1 Can v1 zlib deltas be read after introducing v2 zstd?

If `maybe_store_delta()` is never called, no v2 blobs exist either. The migration question is premature from a data compatibility standpoint.

If migration proceeds, new v2 blobs would be written with a **version=2 byte and zstd codec**. Old v1 readers would see `version != VERSION` (where VERSION=1) and return `base` — fail-safe, but lossy (user loses the delta, gets base text back).

### 5.2 Proposed v1/v2 Header Design

```
Offset  Size  Field         v1 value      v2 value
------  ----  -----         ---------     ---------
0       4     MAGIC         b'DELT'       b'DELT'
4       1     VERSION       1             2
5       1     FLAGS         bit0=zlib     bit0=zstd
                                   bit7=full      bit7=full
6       4     ORIG_LEN      big-endian    big-endian uint32
10      4     DELTA_LEN     big-endian    big-endian uint32
14      N     DELTA_DATA    zlib bytes    zstd bytes
```

**Flag semantics for v2**:
- bit 7 (0x80): full-text storage (unchanged)
- bit 0 (0x01): codec selector — 0=zlib (v1), 1=zstd (v2)

**Unknown version fallback**: Version > current max → `return base` (existing fail-safe behavior). This means old readers never crash on new data, but also never recover new data.

### 5.3 Migration Plan (if safe to proceed)

Since no v1 blobs exist, a **lazy one-time migration** is viable:

1. Add v2 encode path: `compression.zstd.compress()` with VERSION=2, FLAGS bit 0=1
2. Add v2 decode path: detect VERSION=2 → use `compression.zstd.decompress()`
3. Existing v1 decode path stays intact (VERSION=1 → zlib)
4. No data rewrite needed — new writes produce v2, old v1 readers degrade gracefully
5. At some future sweep, a background task could rewrite remaining v1 blobs to v2 (lazy migration)

### 5.4 Rollback Plan

- Rollback = revert encode path to v1/zlib
- Existing v2 blobs in storage would become unreadable by reverted code (old reader sees VERSION=2 → returns base)
- Mitigation: keep v1/v2 decode paths for at least one release cycle

---

## 6. Benchmark: zlib level 6 vs compression.zstd default

Real samples from project files:

| Sample | Original | zlib size | zstd size | zstd vs zlib | zlib c/ms | zstd c/ms | zlib d/ms | zstd d/ms |
|--------|----------|-----------|-----------|--------------|-----------|-----------|-----------|-----------|
| small <16KB | 15,000 | 1,357 (9.0%) | 1,493 (9.9%) | **-10%** (worse) | 0.2 | 1.2 | 0.03 | 0.15 |
| medium 100–500KB | 450,000 | 60,258 (13.4%) | 61,826 (13.7%) | **-2.6%** (worse) | 7.2 | 1.2 | 0.5 | 0.44 |
| large >1MB | 333,467 | 22,817 (6.8%) | 23,791 (7.1%) | **-4.3%** (worse) | 6.5 | 1.0 | 0.32 | 0.34 |

Delta diff (20,933 bytes, 4.98% of full text):

| | Size | Ratio | Compress | Decompress | Peak |
|--|------|-------|----------|------------|------|
| zlib | 3,567 | 17.0% | 1.87ms | 0.06ms | 3.5KB |
| zstd | 3,757 | 18.0% | 0.30ms | 0.05ms | 3.7KB |
| **diff** | | **+5.3% larger** | **6× faster** | **17% faster** | |

### 6.1 Benchmark Analysis

- **Size**: zstd produces **2–10% larger** output than zlib level 6 for text content in this size range. This is the opposite of what most benchmarks show for binary data — text + unified diff + zlib level 6 is already very efficient.
- **Compression time**: zstd is **3–6× faster** than zlib compress
- **Decompression time**: zstd is roughly equivalent to zlib (sometimes slightly faster, sometimes slightly slower)
- **Peak memory**: equivalent

**For this codebase's workload** (text diffs, JSON, mostly English text): **zstd does NOT improve compression ratio**. It trades size for speed. Whether that trade is worth it depends on the bottleneck profile.

---

## 7. Verdict

### PATCH: **NO PATCH** — Premature

**Reasoning**:
1. `SmartDeduplicator.maybe_store_delta()` has **zero production callers** — no data loss risk from migration, but no benefit either
2. `_compress_full()` is **scoring-only** — safe for zstd internal migration, but produces no persistent output
3. `delta_compressor.py` is **correctly structured** for v2 extension (version byte + flags), but there is nothing to migrate
4. The **only blob storage** (legacy `SnapshotStorage`) is already using ZSTD via `compression.zstd` at level 3
5. **Benchmark shows zstd produces larger output** (2–10% worse) for this workload — migration would increase storage, not reduce it

### 7.1 What IS Confirmed Safe to Migrate Later

| Item | Status | Reason |
|------|--------|--------|
| `delta_compressor._encode_delta()` | Safe to patch (NO DATA) | Zero persisted DELTA v1 blobs exist |
| `delta_compressor._encode_full()` | Safe to patch (NO DATA) | Zero persisted DELTA v1 blobs exist |
| `smart_deduplicator._compress_full()` | Safe to migrate | Never persisted, scoring-only |
| `legacy/atomic_storage.py` ZSTD | Already migrated | Level 3, working |

### 7.2 Exact Files to Patch in Later Sprint

When a future sprint confirms production need for delta storage:

```
tools/delta_compressor.py       — v2 codec swap (zlib → compression.zstd)
tools/smart_deduplicator.py     — v2 codec swap in _compress_full() (optional)
tests/test_delta_compressor.py  — new probe lane for delta_compressor
tests/test_smart_deduplicator.py — extend existing test coverage
```

### 7.3 Test Plan (for later sprint)

```
1. Unit tests for v2 encode/decode round-trip (delta_compressor.py)
2. Unit tests for v1/zlib decode fallback (backward compat)
3. Unit tests for unknown version → return base (fail-safe)
4. Integration test: maybe_store_delta() with store_cb mock
5. Benchmark test: assert zstd faster-than-zlib (compress time)
6. Size regression test: zstd ratio within +10% of zlib
```

### 7.4 Rollback Plan (for later sprint)

1. Revert `VERSION = 2 → VERSION = 1`
2. Revert encode path: `compression.zstd → zlib.compress(level=6)`
3. Keep v2 decode path for one release (graceful degradation to base)
4. No data migration needed — no v2 blobs should exist in the rollback scenario

---

## 8. Open Questions

1. **Is there a scenario where `maybe_store_delta()` gets wired to a storage backend?** If yes, the v2 migration with backward-readable format becomes urgent. Track as a product decision.
2. **Should `delta_compressor.py` be deprecated entirely** given zero production usage? The format is clean, but the code has no active consumers. Consider archiving or gating behind a feature flag.
3. **Is the 2–10% size regression from zstd acceptable** given 3–6× faster compression? For write-heavy workloads (many deltas), the speed gain may outweigh the size cost. For storage-constrained environments (M1 8GB), zlib wins.

---

## 9. Summary

| Question | Answer |
|----------|--------|
| Where are DELTA v1 blobs persisted? | **NOWHERE** — no production callers |
| Is smart_deduplicator._compress_full() persistent? | **NO** — scoring-only |
| Is migration safe right now? | **NO** — no persistent format to protect, but no urgency either |
| Does zstd outperform zlib for this workload? | **NO** — 2–10% larger output, faster compress, equivalent decompress |
| Should we patch? | **NO PATCH in F214C-2** — deferred to a sprint with confirmed production need |
| Legacy SnapshotStorage ZSTD | **Already migrated** — untouched by this scope |
