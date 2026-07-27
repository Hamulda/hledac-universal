# Python Conventions

> **Source:** `CLAUDE.md` — Hledac Universal OSINT Orchestrator
> **Last updated:** F350M-R (July 2026)

---

## 1. Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Modules | `snake_case` | `duckdb_store.py`, `async_helpers.py` |
| Classes | `PascalCase` | `DuckDBShadowStore`, `SprintScheduler` |
| Functions | `snake_case` | `async_ingest_findings_batch`, `run_sprint` |
| Methods | `snake_case` | `acquire_budget`, `upsert_ioc` |
| Variables | `snake_case` | `finding_id`, `max_kv_size` |
| Constants | `SCREAMING_SNAKE_CASE` | `MAX_CLAIMS`, `KV_BITS` |
| Private attrs | `_snake_case` (single underscore) | `_pending_findings`, `_circuit_failures` |
| Dunder attrs | `__dunder__` | `__aenter__`, `__slots__` |

### Slots and Private State

All performance-critical classes use `__slots__` to avoid `__dict__` overhead:

```python
class DuckDBShadowStore:
    __slots__ = (
        '_pending_accepted_findings',
        '_graph_ingest_task',
        '_flush_lock',
        ...
    )
```

### Type Aliases

```python
from typing import TypeAlias

IOCGroup: TypeAlias = str
FindingId: TypeAlias = str
URL: TypeAlias = str
```

---

## 2. Async Patterns

### `asyncio.gather` — Always with `return_exceptions=True`

```python
# WRONG — exceptions propagate as exceptions
results = await asyncio.gather(task_a(), task_b())

# CORRECT — failures return as values
results = await asyncio.gather(task_a(), task_b(), return_exceptions=True)
_check_gathered(results)  # always call _check_gathered() after
```

### No `time.sleep()` in Async Code

```python
# WRONG — blocks the event loop
await asyncio.sleep(0.1)   # OK
time.sleep(1)               # BLOCKS

# CORRECT
await asyncio.sleep(1)
await asyncio.to_thread(blocking_io_call)
```

### No `asyncio.run()` in ThreadPoolExecutor

```python
# WRONG — M1 crash vector
future = executor.submit(asyncio.run, coro())

# CORRECT
loop = asyncio.get_running_loop()
future = executor.submit(loop.run_until_complete, coro())
```

### Lazy `asyncio.Lock` Initialization

Use the DCLP pattern to avoid initialization order issues:

```python
# WRONG — module-level __init__ can fail
_lock: asyncio.Lock = asyncio.Lock()

# CORRECT — deferred initialization
class LazyAsyncLock:
    _lock: asyncio.Lock | None = None
    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock
```

### `mx.eval([])` Before `mx.metal.clear_cache()`

```python
# Always pair — without mx.eval, clear_cache is a no-op
import mlx.core as mx
mx.eval([])                   # flush pending ops
mx.metal.clear_cache()        # now actually clears
```

---

## 3. DuckDB Write Path

### Single Canonical Write

```python
# The SOLE canonical write path for DuckDB:
await duckdb_store.async_ingest_findings_batch(findings)

# NEVER write directly:
# cursor.execute("INSERT INTO canonical_findings ...")  # WRONG
# duckdb_store._conn.execute(...)                       # WRONG
```

### LMDB Bulk Write

```python
# WRONG — per-item transaction in a loop
with env.begin(write=True) as txn:
    for key, value in items:
        txn.put(key, value)

# CORRECT — single transaction via putmany
with env.begin(write=True) as txn:
    txn.putmulti([(k, v) for k, v in items])
```

### LMDB Buffer — No `bytes()` Conversion

```python
# WRONG — bytes() on LMDB buffer destroys zero-copy
data = bytes(txn.get(key))   # copies!

# CORRECT — use directly as memoryview
data: memoryview = txn.get(key)  # zero-copy
```

---

## 4. URL Deduplication

```python
# ALWAYS use RotatingBloomFilter
from tools.url_dedup import RotatingBloomFilter

# WRONG — unbounded
seen_urls: set[str] = set()           # ScalableBloomFilter grows without limit

# CORRECT — bounded LRU
dedup = RotatingBloomFilter(max_size=100_000, num_filters=4)
```

---

## 5. MLX / Metal Memory

### KV Cache Config (In `mlx_lm.generate()`, NOT `load()`)

```python
from mlx_lm import generate, load

model, tokenizer = load("mlx-community/Hermes-3-Llama-3.2-3B-4bit")
# KV cache config goes in generate(), NOT load()
response = generate(
    model,
    tokenizer,
    prompt="...",
    kv_bits=4,           # ← HERE
    max_kv_size=8192,    # ← HERE
)
```

### Dynamic Metal Cache Limit

```python
from core.resource_governor import get_dynamic_metal_cache_limit

metal_limit = get_dynamic_metal_cache_limit()  # min(max(available*0.2, 512MiB), 1GiB)
```

---

## 6. Import Patterns

### Lazy Imports (PEP 562)

```python
# brain/__init__.py — PEP 562 __getattr__
def __getattr__(name):
    if name == 'inference_engine':
        from brain.inference_engine import Hermes3Engine as _cls; return _cls()
    ...
    raise AttributeError(name)
```

### Deferred Import Pattern (No Raw `try/except ImportError`)

```python
# WRONG — 7µs cold-start penalty per file
try:
    from otel import instrumented as _otel_instrumented
except ImportError:
    from hledac.universal.otel._instrumentation import instrumented as _otel_instrumented

# CORRECT — zero-cost until first use
from hledac.universal.utils.optional_imports import optional
_otel_instrumented = optional(
    "otel:instrumented",
    default=optional("hledac.universal.otel._instrumentation:instrumented"),
)
```

**Module-level `except ImportError` is ALLOWED inside methods** — legitimate runtime deferral.

---

## 7. Error Handling

### No Bare `except:`

```python
# WRONG
except:
    pass

# CORRECT
except Exception:
    pass

# CORRECT — specific types
except (ValueError, TypeError) as e:
    log.error(f"Invalid config: {e}")
```

### Fail-Safe Sidecars

Sidecars must return `[]` on failure, never raise:

```python
async def run_async(self, ctx: SidecarContext) -> list[Any]:
    try:
        return await self._do_work(ctx)
    except Exception:
        return []  # fail-safe
```

---

## 8. Feature Flags

All feature flags are **always-on by default** (no feature toggles for new features).
Opt-out via environment variable only.

| Flag Pattern | Purpose |
|-------------|---------|
| `HLEDAC_ENABLE_*` | Feature activation (0=off, 1=on) |
| `HLEDAC_*` | Configuration (thread count, cache size, etc.) |

```python
# WRONG — new features behind flags
if os.environ.get('HLEDAC_ENABLE_NEW_FEATURE'):
    await do_new_feature()

# CORRECT — always-on
await do_new_feature()
```

---

## 9. Exit Codes (F350M-R)

| Code | Meaning | Trigger |
|------|---------|---------|
| `0` | Clean success | Sprint completed |
| `1` | Runtime error | `except Exception` |
| `2` | Config error | F221-ABORT, argparse error |
| `3` | Programmer error | NameError, AttributeError, ImportError |
| `130` | SIGINT | `KeyboardInterrupt` |

```python
sys.exit(0)   # clean
sys.exit(2)   # config error (F221 guard)
sys.exit(3)   # programmer error (NameError deep)
```

---

## 10. Testing Conventions

### Test File Naming

```
tests/
├── test_{module}.py           # unit tests
├── test_{feature}_{aspect}.py # e.g., test_sprint_f273.py
├── integration/                # integration test suite
├── rust/                      # Rust extension tests
└── probe_*/                   # probe test suites
```

### Async Test Fixtures

```python
@pytest.fixture
async def store(duckdb_store):
    await store.async_initialize_schema()
    yield store
    await store.aclose()

@pytest.mark.asyncio
async def test_ingest(store):
    result = await store.async_ingest_findings_batch([finding])
    assert result[0].decision == 'accept'
```

### Subprocess Exit Code Tests

```python
def test_f221_abort_guard():
    """Sprint duration below MIN_ACTIVE_WINDOW_S must exit(2)."""
    result = subprocess.run(
        ['python', '-m', 'hledac.universal', '--sprint', 'x', '--duration', '30'],
        capture_output=True,
    )
    assert result.returncode == 2
```

---

## 11. Sprint Lifecycle Invariants

```
Minimum sprint duration = effective_windup_lead + 30s
windup_lead_effective = 30% of duration, clamp [30, 180]s

--duration 60   → windup=30s, active=30s ✅ (active=MIN_ACTIVE_WINDOW_S)
--duration 30   → F221-ABORT exit(2) ❌
--duration 30 --force → [F221-FORCED] warning, continues
```

---

## 12. Critical Anti-Patterns (M1 8GB)

| Anti-pattern | Crash/Safety Risk | Fix |
|-------------|-------------------|-----|
| `asyncio.run()` in executor | M1 panic | `loop.run_until_complete()` |
| `time.sleep()` in async | Event loop stall | `asyncio.sleep()` |
| `mx.eval([])` before `clear_cache()` | Metal cache leak | Always pair |
| `ScalableBloomFilter` | Unbounded RAM | `RotatingBloomFilter` |
| `bytes()` on LMDB buffer | Zero-copy destroyed | Use `memoryview` directly |
| `early MLX import` | M1 crash | Lazy import via `__getattr__` |
| `--disable-gpu` in nodriver | Slow (GPU=CPU on M1) | Omit flag |
| `aggressive_cleanup()` without `()` | No-op | `await obj.aggressive_cleanup()` |
