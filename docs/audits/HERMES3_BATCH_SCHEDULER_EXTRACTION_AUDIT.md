# Hermes3 Batch Scheduler Extraction Audit

## Summary

Batch scheduling policy CAN be extracted as `BatchScheduler` — a pure asyncio class with NO MLX/GPU dependencies. The seam is at `_is_batch_safe()` / `generate_structured()` routing decision.

---

## Component Map

### 1. Queue Data Structures

| Field | Type | Location | Purpose |
|-------|------|----------|---------|
| `_batch_queue` | `asyncio.PriorityQueue(maxsize=256)` | line 275 | Items: `(priority, tie, schema_key, payload)` |
| `_batch_tie_breaker` | `itertools.count()` | line 330 | FIFO tie-breaking for equal priority |
| `_pending_futures` | `set()` | line 310 | In-flight futures for emergency failure handling |
| `_flush_cycle_count` | `int` | line 314 | Counter for age bump interval |
| `_last_age_bump` | `int` | line 316 | Last cycle where age bump occurred |

**Queue item tuple**: `(priority: float, tie: int, schema_key: str, payload: dict)`
- `priority`: lower = higher priority (0 = highest, bypasses batch)
- `tie`: itertools count for FIFO within same priority
- `schema_key`: `response_model.__name__` for schema boundary segregation
- `payload`: `{"prompt", "response_model", "temperature", "max_tokens", "system_msg", "future"}`

### 2. Worker Loop (`_batch_worker`) — line 449

```
while True:
  → Emergency unload check → fail pending futures, break
  → Poison pill (shutdown flag) check → fail pending futures, break
  → Adaptive flush interval (3-tier: 2.0s / 1.0s / 0.5s)
  → wait_for(first_item, timeout=flush_interval)
  → Extract schema_key, prompt_hash, length_bin from first item
  → Gather up to _batch_max_size items with boundary checks:
      - Schema mismatch → putback, increment counter
      - Prompt hash mismatch → putback, increment counter
      - Length bin mismatch → putback, increment counter
  → Age bump every _age_bump_interval cycles
  → Update queue_depth EMA
  → Process batch → _process_batch() → _process_structured_batch()
  → Update batch_size + dispatch_to_result_ms EMA
```

**Key behaviors**:
- Schema boundary: prevents mixing different Pydantic/msgspec types
- Prompt hash boundary: prevents mixing different system prompts (padding waste)
- Length bin boundary: short/medium/long segregation
- Age bump: priority -= 1 every 3 flush cycles (anti-starvation)

### 3. Priority / Age Bumping Policy

**Anti-starvation**: `_age_bump_queue()` (line 633)
```
- Extract all items from queue (get_nowait loop)
- Re-enqueue with new_priority = max(0, priority - 1)
- Called every _age_bump_interval (= 3) flush cycles
```

**Routing priority**:
- `priority=0`: urgent → bypasses batch, single-path only
- `priority>0`: batch-safe if `_is_batch_safe()` returns True

### 4. Flush Policy

`_current_flush_interval()` (line 567) — 3-tier adaptive:

| Queue Depth | Flush Interval |
|-------------|----------------|
| > 192 (high pressure) | 0.5s (fast) |
| > 64 (medium pressure) | 1.0s (medium) |
| otherwise | 2.0s (default) |

`_batch_max_size = 8` — max items per batch

### 5. MLX / Model Calls (NOT extractable — stay in Hermes3Engine)

| Method | Line | MLX dependency |
|--------|------|----------------|
| `_run_structured_single()` | 719 | → `generate_structured_safe()` via executor (inference!) |
| `_execute_structured_batch()` | 707 | → loops `_run_structured_single()` (sequential, GPU constraint) |
| `_process_structured_batch()` | 676 | → calls `_execute_structured_batch()` |
| `_process_batch()` | 649 | → branches on `payload.get('type') == 'structured'` |
| `_get_gpu_memory()` | 776 | → `mx.get_active_memory()` direct |
| `_safe_mlx_eval_and_clear_cache()` | 116 | → `mx.eval()`, `mx.metal.clear_cache()` |
| KV cache objects | — | `_prompt_cache`, `_system_prompt_cache`, `_warmup_cache` |

### 6. GPU Memory Tracking (NOT extractable — stay in Hermes3Engine)

`_get_gpu_memory()` (line 776):
```python
def _get_gpu_memory(self) -> int:
    if not _MLX_AVAILABLE_GLOBAL:
        return 0
    import mlx.core as mx
    if hasattr(mx, 'get_active_memory'):
        return mx.get_active_memory()
    elif hasattr(mx.metal, 'get_active_memory'):
        return mx.metal.get_active_memory()
```

### 7. Cache Management (NOT extractable — stay in Hermes3Engine)

- `_warmup_cache`: isolated warmup KV cache (line 319)
- `_prompt_cache`: MLX prompt cache for generation (line 250)
- `_system_prompt_cache`: persistent system-prompt cache (line 250)
- `_prefix_cache`: bounded LRU for tokenization (line 260)

### 8. Telemetry Counters

**EMA metrics** (`_telemetry_ema`) — pure Python, updated by batch worker:
- `enqueue_to_dispatch_ms`: time from enqueue to dispatch start
- `dispatch_to_result_ms`: time from dispatch start to result
- `batch_size`: current batch size
- `queue_depth`: current queue size

**Counters** (`_telemetry_counters`) — pure Python:
| Counter | Purpose |
|---------|---------|
| `batch_submitted` | Submitted to batch queue |
| `batch_executed` | Batch execution succeeded |
| `batch_fallback_single` | Batch failed, fell back to single |
| `batch_shattered` | Batch parse failed, retried individually |
| `schema_mismatch_flushes` | Schema boundary putback |
| `prompt_mismatch_flushes` | Prompt hash boundary putback |
| `length_bin_mismatch_flushes` | Length bin boundary putback |
| `emergency_guard_triggered` | Emergency unload before inference |
| `emergency_batch_rejected` | Rejected at emergency guard |
| `emergency_pending_failed` | Pending futures failed on emergency |
| `adaptive_flush_default/medium/fast_entries` | Flush tier selection |

---

## Extractability Assessment

### EXTRACTABLE → BatchScheduler

| Component | Reason |
|-----------|--------|
| `_batch_queue` (PriorityQueue) | Pure asyncio, no MLX |
| `_batch_tie_breaker` | Pure itertools, no MLX |
| `_pending_futures` | Pure Python set |
| `_flush_cycle_count`, `_last_age_bump` | Pure counters |
| `_age_bump_interval` | Pure int config |
| `_batch_max_size` | Pure int config |
| `_batch_default_flush_interval` | Pure float config |
| `_batch_medium_pressure_depth` | Pure int config |
| `_batch_high_pressure_depth` | Pure int config |
| `_age_bump_queue()` | Pure asyncio queue ops |
| `_current_flush_interval()` | Pure Python, no MLX |
| `_compute_length_bin()` | Pure Python string ops |
| `_compute_system_prompt_hash()` | Pure Python hashlib |
| `_is_batch_safe()` | Pure Python checks (no MLX) |

### NOT EXTRACTABLE → Stay in Hermes3Engine

| Component | Reason |
|-----------|--------|
| `_batch_worker()` | Calls `_process_batch()` → MLX inference |
| `_process_batch()` | Calls `_process_structured_batch()` → MLX |
| `_process_structured_batch()` | Calls `_execute_structured_batch()` → MLX |
| `_execute_structured_batch()` | Loops `_run_structured_single()` → MLX inference |
| `_run_structured_single()` | Calls `generate_structured_safe()` → MLX inference |
| `_get_gpu_memory()` | Directly calls `mx.get_active_memory()` |
| `_safe_mlx_eval_and_clear_cache()` | Directly calls `mx.eval()`, `mx.metal.clear_cache()` |
| `_warmup_cache`, `_prompt_cache`, `_system_prompt_cache` | MLX cache objects |
| `_get_prefix_cache()` | Returns MLX cache objects |
| `invalidate_prefix_cache()` | Operates on MLX-backed `_prefix_cache` |

### BOUNDARY: Routing Decision

`generate_structured()` (line 1698) is the routing decision point:
```python
if self._is_batch_safe(response_model, priority, stream=False, timeout_s=timeout_s):
    # → batch queue path
    future = await self._submit_structured_batch(...)
    result = await future
else:
    # → direct outlines/JSON path
    ...
```

The `_is_batch_safe()` check is pure Python — can be called from Hermes3Engine without MLX. The routing decision (batch vs direct) stays in Hermes3Engine. The `submit` call is pure asyncio enqueue.

---

## BatchScheduler Interface (proposed)

```python
class BatchScheduler:
    """Pure asyncio batch scheduler — no MLX/GPU dependencies."""

    def __init__(
        self,
        max_size: int = 8,
        max_queue: int = 256,
        default_flush_interval: float = 2.0,
        medium_pressure_depth: int = 64,
        high_pressure_depth: int = 192,
        age_bump_interval: int = 3,
        ema_alpha: float = 0.3,
    ): ...

    async def start(self) -> None: ...
    async def shutdown(self, timeout: float = 3.0) -> None: ...

    async def submit(
        self,
        prompt: str,
        response_model: type,
        priority: float = 1.0,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        system_msg: str = None,
    ) -> asyncio.Future: ...

    def is_batch_safe(
        self,
        response_model: type,
        priority: float,
        timeout_s: Optional[float] = None,
    ) -> bool: ...

    async def flush(self, timeout: float = 5.0) -> int: ...

    def get_telemetry(self) -> dict: ...

    # Internal — for testing
    async def _age_bump_queue(self) -> None: ...
    def _compute_length_bin(self, prompt: str) -> str: ...
    def _compute_system_prompt_hash(self, system_msg: Optional[str]) -> str: ...
```

---

## Seams

### Seam 1: Submit (Hermes3Engine → BatchScheduler)

```
Hermes3Engine.generate_structured():
  if self._is_batch_safe(...):
      future = await self._submit_structured_batch(...)  # pure asyncio enqueue
```

`_submit_structured_batch()` is pure asyncio — enqueues to PriorityQueue and returns Future. No MLX involvement.

### Seam 2: Execution (BatchScheduler → Hermes3Engine)

Batch worker calls `_run_structured_single(payload)` which calls `generate_structured_safe()` — this is MLX inference. The execution callback must be injected or called back into Hermes3Engine.

**Option A**: Injection — pass `execute_callback` to BatchScheduler
**Option B**: Interface — Hermes3Engine implements `execute_single(payload)` method, BatchScheduler calls `self._engine.execute_single(payload)`
**Option C (chosen)**: BatchScheduler calls `asyncio.ensure_future(coro)` where coro is a method on the engine instance passed at construction

### Seam 3: Shutdown Coordination

`Hermes3Engine.emergency_unload()` calls `_shutdown_batch_worker()` which:
1. Sets `_batch_worker_shutting_down = True` (poison pill)
2. Cancels `_batch_worker_task` with 3s timeout
3. Fails all pending futures

BatchScheduler must expose equivalent shutdown protocol.

---

## Implementation Plan

### Phase 1 (THIS AUDIT — no code changes)
- [x] Audit complete → this document
- [ ] Write audit doc to `docs/audits/HERMES3_BATCH_SCHEDULER_EXTRACTION_AUDIT.md`

### Phase 2 (First safe step — NO production reconnection)
- Create `brain/batch_scheduler.py` as pure class
- Zero MLX imports
- Unit tests only with fake jobs and AsyncMock
- Does NOT connect to Hermes3Engine

### Phase 3 (Integration — future sprint)
- Inject BatchScheduler into Hermes3Engine as optional layer
- Preserve `generate_structured()` public API
- Run existing test suite (no MLX load in tests)

---

## Invariants

| ID | Description |
|----|-------------|
| B.S1 | BatchScheduler has zero MLX imports |
| B.S2 | No GPU memory tracking in BatchScheduler |
| B.S3 | No KV cache objects in BatchScheduler |
| B.S4 | Worker shutdown bounded ≤ 3s |
| B.S5 | Pending futures failed on shutdown |
| B.S6 | Queue maxsize ≤ 256 |
| B.S7 | Age bump interval ≥ 1 |
| B.S8 | flush_interval ≥ 0.5s |

---

## Test Strategy (Phase 2 only)

```
tests/test_batch_scheduler.py
├── test_queue_initialization
├── test_priority_ordering (priority 0 bypasses, priority 1+ enqueues)
├── test_age_bump_every_N_cycles
├── test_flush_interval_adaptive (depth > 192 → 0.5s, > 64 → 1.0s)
├── test_schema_boundary_segregation
├── test_prompt_hash_boundary_segregation  
├── test_length_bin_boundary_segregation
├── test_batch_max_size_limit
├── test_shutdown_poison_pill
├── test_pending_futures_failed_on_shutdown
├── test_is_batch_safe_routing
├── test_telemetry EMA updates
└── test_concurrent_enqueue_dequeue
```

**No MLX model loading in any test.**
**No GPU side effects.**
**All tests use AsyncMock + fake payloads.**