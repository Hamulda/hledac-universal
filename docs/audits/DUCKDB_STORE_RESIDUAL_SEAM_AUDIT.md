# DuckDBShadowStore Residual Seam Audit

**Date:** 2026-05-18
**Sprint:** F223
**Status:** COMPLETE

## Manager Initialization Map

### WALManager — initialized in 2 places

| Location | Line | Pattern | Called from |
|----------|------|---------|-------------|
| `async_initialize()` | 1910–1914 | `if self._wal_manager is None` | `initialize()` → canonical init path |
| `async_ingest_findings_batch()` | 4388–4400 | `if not hasattr(self, "_wal_manager") or self._wal_manager is None` | batch ingest (lazy fallback) |

### DedupManager — initialized in 1 place

| Location | Line | Pattern | Called from |
|----------|------|---------|-------------|
| `async_initialize()` | 1918–1920 | `if self._dedup_manager is None` | `initialize()` → canonical init path |

**No lazy fallback for DedupManager in ingest path** — correct, DedupManager is initialized in `async_initialize()` before any ingest is called.

## Legacy Inline LMDB Patterns (Pre-F216G Extraction)

There are **5 locations** where `_wal_lmdb` is initialized directly via `LMDBKVStore` instead of going through `WALManager`:

| Method | Line | Purpose | WALManager Equivalent |
|--------|------|---------|----------------------|
| `_sync_update_finding_payload_text()` | 4813–4827 | Read/update payload_text on a finding | `WALManager.wal_get()` / `wal_put()` |
| `_sync_read_envelope()` | 4891–4895 | Read finding envelope | `WALManager.wal_get()` |
| `_wal_write_pending_sync_marker()` | 5844–5867 | Write pending sync marker | `WALManager.wal_write_pending_sync_marker()` |
| `_wal_put()` | 5683–5706 | Write WAL entry | `WALManager.wal_put()` |
| `_activation_record_findings_batch()` | 6087–6109 | Batch WAL write | `WALManager.wal_put_many()` |

All 5 use the same inline init pattern:
```python
if not hasattr(self, "_wal_lmdb"):
    _wal_root = self._db_path.parent if self._db_path else None
    if _wal_root is None:
        return False
    self._wal_lmdb = LMDBKVStore(path=str(_wal_root / "shadow_wal.lmdb"))
```

**Impact:** These 5 methods bypass WALManager and use raw LMDBKVStore directly on the same `shadow_wal.lmdb` file. WALManager is a wrapper over the same LMDB file. The two paths are not coordinated — WALManager has its own open LMDB env, these methods open a separate LMDBKVStore instance.

**Correct behavior:** Both WALManager and LMDBKVStore open the same `shadow_wal.lmdb` file. WALManager maintains WAL semantics (append-only, pending markers). The raw LMDBKVStore accesses in these 5 methods should use WALManager methods instead.

## `_startup_replay_done` State Ownership

| Property | Value | Where set |
|----------|-------|-----------|
| `self._startup_replay_done` | bool, default `False` | Line 583 (init), reset at lines 1841, 4977, 6160 |
| `startup_replay_done()` property | Returns `self._startup_replay_done` | Line 5572 |

**Assessment:** `_startup_replay_done` is store-state (not WALManager state). It tracks whether the bounded startup replay has run — an initialization concern, not a WAL concern. Correctly owned by DuckDBShadowStore. **No extraction needed.**

## Legacy Fallback Patterns (Pre-F216G)

| Pattern | Found at | Notes |
|---------|----------|-------|
| `hasattr(self, "_wal_manager")` | Lines 3759, 4388 | Used in ingest path — lazy init for WALManager (correct) |
| `hasattr(self, "_wal_lmdb")` | Lines 4813, 4891, 5844, 5683, 6087 | Used in 5 legacy methods — should use WALManager |

## Cleanup Candidates (Safe Micro-Extractions)

### 1. `_wal_write_pending_sync_marker` → WALManager (SAFE)

This is a pure delegation method. WALManager already has `wal_write_pending_sync_marker()` with identical semantics.

**Before:**
```python
def _wal_write_pending_sync_marker(self, finding_id: str, query: str, source_type: str, confidence: float) -> bool:
    if not hasattr(self, "_wal_lmdb"):
        self._wal_lmdb = LMDBKVStore(path=...)
    # ... inline LMDB put
```

**After:** Call `self._wal_manager.wal_write_pending_sync_marker(...)` directly. Remove `_wal_write_pending_sync_marker` method.

**Risk:** LOW. The WALManager method already exists and has identical signature.

### 2. `_wal_put` → WALManager (SAFE)

Same as above — WALManager has `wal_put()`. The inline method is a duplicate.

### 3. `_activation_record_findings_batch` LMDB path → WALManager (SAFE)

WALManager has `wal_put_many()`. This is a batch write that can delegate.

### 4. `_sync_update_finding_payload_text` and `_sync_read_envelope`

These read/write payload_text. WALManager's `wal_get` and `wal_put` can replace the raw LMDB access. Requires verifying WALManager.wal_put handles re-writing an existing key (it does — WAL is append-only for this key space, updates are fine).

## Methods NOT Safe for Extraction (Canonical Write Core)

- `async_ingest_findings_batch()` — canonical write path, stays as-is
- `async_record_shadow_findings_batch()` — canonical write path, stays as-is
- `_bounded_startup_replay()` — uses raw LMDB to scan pending markers during boot (before WALManager exists), special case

## Recommendation

Perform 3-method cleanup in order:
1. `_wal_write_pending_sync_marker` → replace with `WALManager.wal_write_pending_sync_marker()`
2. `_wal_put` → replace with `WALManager.wal_put()`
3. `_activation_record_findings_batch` LMDB block → replace with `WALManager.wal_put_many()`

Then clean up the two `_wal_lmdb` legacy accessors (`_sync_update_finding_payload_text`, `_sync_read_envelope`) using `WALManager.wal_get`/`wal_put`.

**Do NOT extract:**
- `_startup_replay_done` (store-state, not WAL state)
- `_bounded_startup_replay()` (pre-manager boot sequence)
- DedupManager initialization (already canonical, single init site)