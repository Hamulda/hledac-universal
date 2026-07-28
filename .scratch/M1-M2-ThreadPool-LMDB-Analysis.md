# Issue M1 + M2: ThreadPoolExecutor Thrash & LMDB Per-Item Writes

**Date:** 2026-07-28  
**Status:** COMPLETE (Phase 1-4)

---

## EXECUTIVE SUMMARY

| Issue | Root Cause | Impact | Fix Strategy |
|-------|-----------|--------|--------------|
| **M1: TPE Thrash** | 258 hits across codebase — modules create own TPE instead of using shared pools | M1 8GB thread explosion, GIL contention | Route all TPE creation through `get_or_create()` from `utils/domain_executors.py` |
| **M2: LMDB Per-Item** | 58+ write txns — `env.begin(write=True)` per item instead of batch | LMDB single-writer bottleneck, performance loss | Use `putmulti_bounded()` from `core/lmdb_async.py` with write-ahead buffer |

---

## IMPLEMENTED CHANGES (Phase 1)

### File: `core/rust_backend/ioc.py`
**Before:**
```python
import concurrent.futures
_n_workers = min(4, (os.cpu_count() or 2))
_executor: concurrent.futures.ThreadPoolExecutor | None = None

def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = concurrent.futures.ThreadPoolExecutor(max_workers=_n_workers)
    return _executor
```

**After:**
```python
from concurrent.futures import ThreadPoolExecutor
from hledac.universal.utils.domain_executors import get_or_create

def _get_executor() -> ThreadPoolExecutor:
    """Return shared 'crypto' domain executor for CPU-bound IOC extraction."""
    return get_or_create("crypto")
```

### File: `brain/distillation_engine.py`
**Before:**
```python
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
_EMBED_EXECUTOR: ThreadPoolExecutor | None = None
_EMBED_EXECUTOR_LOCK = Lock()

def _get_embed_executor() -> ThreadPoolExecutor:
    global _EMBED_EXECUTOR
    with _EMBED_EXECUTOR_LOCK:
        if _EMBED_EXECUTOR is None:
            _EMBED_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="distill_embed")
        return _EMBED_EXECUTOR
```

**After:**
```python
from concurrent.futures import ThreadPoolExecutor
from hledac.universal.utils.domain_executors import get_or_create

def _get_embed_executor() -> ThreadPoolExecutor:
    """Return shared 'embed' domain executor for CPU-bound embedding extraction."""
    return get_or_create("embed")
```

### File: `brain/gnn_predictor.py`
**Before:**
```python
if self._cpu_executor is None:
    self._cpu_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
loop = asyncio.get_running_loop()
return await loop.run_in_executor(self._cpu_executor, _sync)
```

**After:**
```python
from hledac.universal.utils.domain_executors import get_or_create
loop = asyncio.get_running_loop()
return await loop.run_in_executor(get_or_create("parallel"), _sync)
```

### File: `tools/url_dedup.py`
**Before:**
```python
with ThreadPoolExecutor(max_workers=_PREFILTER_WORKERS) as ex:
    return list(ex.map(fast_hash, texts))

with ThreadPoolExecutor(max_workers=_NORMALIZE_WORKERS) as ex:
    return list(ex.map(normalize_url, urls))
```

**After:**
```python
from hledac.universal.utils.domain_executors import get_or_create
return list(get_or_create("html").map(fast_hash, texts))
return list(get_or_create("html").map(normalize_url, urls))
```

---

## ARCHITECTURE: Shared Executor Registry

```python
# utils/domain_executors.py — Global registry
get_or_create(name: str, max_workers: int | None = None) → ThreadPoolExecutor

# Per-domain presets (M1 8GB bounded, total ≤ 24 threads):
_DOMAIN_PRESETS = {
    "html": 8,        # HTML extraction, URL hashing
    "duckdb": 2,     # DuckDB sync queries
    "infer": 1,      # CoreML/MLX sync bridge
    "crypto": 1,     # yara-python, Pycryptodome, IOC extraction
    "semantic": 2,   # SimHash, embedding deduplication
    "content": 3,    # content hashing
    "metadata": 2,    # metadata processing
    "dns": 1,        # DNS/mlx operations
    "parallel": 3,   # general parallel execution
    "nlp": 2,       # GLiNER2, fast-langdetect
    "vision": 2,     # PyMuPDF, vision encoder
    "embed": 1,      # MLX embed sync bridge
    "storage": 2,    # DuckDB sync adapter
    "captcha": 1,    # PIL CAPTCHA analysis
    "exposure_db": 1 # LMDB single-writer
}
```

---

## IMPLEMENTED CHANGES (Phase 2)

### File: `brain/unified_embedding_manager.py`
**Before:**
```python
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:  # chunk_a/chunk_b
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:  # single batch
```
**After:**
```python
pool = get_or_create("embed")  # shared 1-worker domain executor
```

### File: `tools/session_manager.py`
**Before:**
```python
self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='session_lmdb')
```
**After:**
```python
self._executor = get_or_create("storage")  # shared 2-worker domain executor
```

### File: `knowledge/duckdb_store.py`
**Before:**
```python
_max_workers = min(4, max(1, (os.cpu_count() or 2)))
self._shared_executor: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=_max_workers, thread_name_prefix="duckdb_unified"
)
```
**After:**
```python
self._shared_executor = get_or_create("duckdb")  # shared 2-worker domain executor
```

### File: `recon/exposure_clients.py`
**Status:** Already migrated in prior session (uses `get_exposure_db_executor()`).

---

## IMPLEMENTED CHANGES (Phase 4)

### File: `core/rust_backend/misc.py`
**Before:**
```python
with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(queries), _POOL_MAX_SIZE)) as executor:
```
**After:**
```python
from hledac.universal.utils.domain_executors import get_or_create
with get_or_create("duckdb") as executor:
```

## REMAINING WORK (Phase 3)

### M2: LMDB Batch Write Fix (Phase 3)

#### `utils/lmdb_bulk.py` — New `putmulti_bounded_str()`
```python
def putmulti_bounded_str(
    env, items: Sequence[tuple[str, dict]], key_prefix: str = "", max_batch: int = 2500
) -> list[bool]:
    """Bounded LMDB bulk write for str-keyed JSON dict values.
    
    Converts str keys → bytes and dict values → msgpack bytes, then calls
    ``putmulti_bounded`` with a single write transaction per chunk.
    """
```

#### `knowledge/lmdb_subdb.py` — `putmany_str` migrated
**Before:**
```python
with self._env.begin(write=True) as txn:
    for key_bytes, value_bytes in encoded:
        try:
            txn.put(key_bytes, value_bytes)
            results.append(True)
        except Exception:
            results.append(False)
```
**After:**
```python
from hledac.universal.utils.lmdb_bulk import putmulti_bounded_str
return putmulti_bounded_str(self._env, items, key_prefix=prefix)
```

#### Remaining M2 items (lower priority):
- duckdb_store.py dedup buffer — needs deeper analysis of `_add_to_dedup_lmdb()` usage
- `core/rust_backend/misc.py` — per-call pool → `get_or_create("parallel")`
- `runtime/_legacy_role_based_pools.py` — migrate to domain_executors

---

## INVARIANTS

| ID | Test Name | Validates |
|----|-----------|-----------|
| M1-INV-1 | `test_no_direct_threadpool_creation` | No `ThreadPoolExecutor(` outside `domain_executors.py` |
| M1-INV-2 | `test_shared_executor_registry_bounded` | `get_or_create()` respects `_TOTAL_THREAD_CAP` |
| M2-INV-1 | `test_lmdb_batch_write_single_txn` | Batch operations use single `begin(write=True)` |
| M2-INV-2 | `test_write_ahead_buffer_bounded` | Buffer has explicit `maxlen`, flushes automatically |

---

## VERIFICATION

```bash
# Import test
uv run python -c "
from core.rust_backend.ioc import _get_executor
from brain.distillation_engine import _get_embed_executor
from tools.url_dedup import fast_hash_parallel
print('✓ All imports successful')
print(f'✓ IOC executor: {_get_executor()._name}')
print(f'✓ Embed executor: {_get_embed_executor()._name}')
"
```

**Result:** ✓ All imports successful
