# asyncio.Queue Bounded Audit — S1

> **Scope:** Project-wide audit všech `asyncio.Queue` instancí.
> Cíl: Zajistit, že každá `asyncio.Queue` má dokumentovaný `maxsize` + overflow strategii.

---

## S1-01 — Unbounded Queues: akce

**Zdroj dat:** Automated audit (`tools/audit/bounded_queue_audit.py`).

| Kategorie | Count | Akce |
|-----------|-------|-------|
| Third-party (.venv-test/) | 50+ | Ignorovat — externí kód |
| Archive (sprint_scheduler_v1_archived.py) | 10+ | Ignorovat — deprecated |
| Test probes (tests/archive/) | 20+ | Ignorovat — test fixtures |
| **Hledac universal (legitimní)** | **~14** | **Review individually** |

### Legitimní unbounded — kategorie A (OK)

```python
# evidence_log.py:468 — F320-ISSUE12 asyncio fallback, kapacita odvozena z memory budget
self._queue = asyncio.Queue(maxsize=capped)  # capped = memory_budget / ITEM_SIZE

# evidence_log.py:1738 — Fallback: asyncio.Queue.put_nowait() loop
# Jen když Rust MPSC není dostupný — fail-soft fallback, unbounded je dočasný stav

# pipeline/_stage_protocol.py:184 — maxsize=self.maxsize (runtime-resolved)
object.__setattr__(self, "_queue", asyncio.Queue(maxsize=self.maxsize))

# pipeline/_stage_protocol.py:220 — new_max resolved at runtime
new_queue = asyncio.Queue[T_out](maxsize=new_max)

# core/sync_bridge.py:85 — maxsize=0 je záměr: "unbounded" pro sync bridge
q: asyncio.Queue[_T] = asyncio.Queue(maxsize=0)  # unbounded
```

### Legitimní bounded — kategorie B (OK)

```python
# evidence_log.py:420 — maxsize=500, derived from memory budget
asyncio_fallback: If True, create asyncio.Queue fallback (for JSONL path).
self._queue = asyncio.Queue(maxsize=500)

# pipeline/_stage_protocol.py:166 — maxsize=0 je init-hack (real maxsize nastaven později)
_queue: asyncio.Queue[T_out] = field(default_factory=lambda: asyncio.Queue(maxsize=0))

# pipeline/ioc_cooccurrence_miner.py:359 — maxsize=100
self._prefetch_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=100)

# layers/communication_layer.py:39 — maxsize=64 per subscriber
self._subscribers[agent_id] = _Subscriber(agent_id=agent_id,
    queue=asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE))

# layers/communication_layer.py:319 — maxsize=256
self._batch_queue: asyncio.Queue = asyncio.Queue(maxsize=256)

# runtime/resource_governor.py:179,277 — maxsize=64
self._worker_adjust_queue: asyncio.Queue[int] = asyncio.Queue(maxsize=64)

# utils/two_pass_pipeline.py:5,85 — maxsize=512
# Backpressure via asyncio.Queue(maxsize=512).

# utils/async_utils.py:146 — maxsize=max_concurrent * 2
q: asyncio.Queue = asyncio.Queue(maxsize=max_concurrent * 2)

# utils/jsonl_lz4_writer.py:99 — maxsize=queue_max
self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_max)

# knowledge/lancedb_store.py:156 — maxsize=100 per table
_queues[table_name] = asyncio.Queue(maxsize=100)

# knowledge/pipelined_ingestor.py:15,114 — maxsize=2 cross-batch pipeline
_pipeline_queue = asyncio.Queue(maxsize=_PIPELINE_QUEUE_MAXSIZE)

# knowledge/analytics_hook.py:52 — maxsize=200, put_nowait only
self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)

# knowledge/graph_rag.py:1695 — maxsize=10
queue: asyncio.Queue = asyncio.Queue(maxsize=10)

# prefetch/prefetch_cache.py:30 — maxsize=maxsize (runtime)
self._q: asyncio.Queue[tuple[str, str, Any]] = asyncio.Queue(maxsize=maxsize)

# brain/continuous_batch_engine.py:72 — maxsize=128
self._queue: asyncio.Queue = asyncio.Queue(maxsize=128)

# brain/ner_engine.py:274 — maxsize=16
queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=16)

# transport/nym_transport.py:64 — maxsize=max_queue_size (runtime)
self._outgoing_queue = asyncio.Queue(maxsize=max_queue_size)

# tool_exec_log.py:180 — maxsize=_WRITE_QUEUE_MAXSIZE
self._write_queue: asyncio.Queue = asyncio.Queue(maxsize=self._WRITE_QUEUE_MAXSIZE)

# pipeline/finding_pipeline.py:97 — maxsize=_PIPELINE_QUEUE_SIZE
self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_PIPELINE_QUEUE_SIZE)

# tools/lightpanda_pool.py:27 — maxsize=min(size*2, _POOL_QUEUE_MAX)
self._available: asyncio.Queue = asyncio.Queue(maxsize=min(size * 2, _POOL_QUEUE_MAX))

# core/observability_async_handler.py:105 — maxsize=maxsize (runtime)
self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)

# research/parallel_scheduler.py:128,131 — PriorityQueue (ne asyncio.Queue)
] = asyncio.PriorityQueue()  # thread-safe internal impl

# recon/dark_web_intelligence.py:271,345 — maxsize=MAX_URL_QUEUE (runtime)
self.url_queue: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_URL_QUEUE)
```

---

## S1-02 — Nové patterny (post-audit)

### Pattern: Producer v ThreadPoolExecutor

**CRITICAL (S1-02 FIX):** `asyncio.Queue.put()` je **coroutine** v Python 3.10+.
Nelze volat z executor thread (only `put_nowait` je sync).

```python
# WRONG — crash na Python 3.10+:
def producer_in_thread(queue: asyncio.Queue, item):
    await queue.put(item)  # TypeError: coroutine cannot be awaited in executor

# CORRECT:
def producer_in_thread(queue: asyncio.Queue, item):
    queue.put_nowait(item)  # put_nowait is sync

# pokud potřebujete backpressure na plnou frontu:
def producer_in_thread_bounded(queue: asyncio.Queue, item):
    try:
        queue.put_nowait(item)  # raises QueueFull if full
    except asyncio.QueueFull:
        pass  # drop on full — or log metric
```

**Reference:** `tests/test_backpressure.py:116` — `asyncio.Queue.put() is a COROUTINE in Python 3.10+`

### Pattern: Rust MPSC místo asyncio.Queue pro high-throughput

**F320-ISSUE12:** `_RustMPSCBytes` (Rust MPSCPool) nahrazuje `asyncio.Queue`
v evidence_log flush path. Výkon: 5–10× speedup přes ARM LSE atomics.

```python
# evidence_log.py — prefer this over asyncio.Queue for bytes flush:
if rust_mpsc_available():
    self._mpsc = _RustMPSCBytes(capacity=4096)  # Rust side
else:
    self._queue = asyncio.Queue(maxsize=capped)   # Python fallback
```

### Pattern: BoundedStageQueue — stage protocol wrapper

```python
# pipeline/_stage_protocol.py:140–265
@dataclass
class BoundedStageQueue(Generic[T_out]):
    """asyncio.Queue s bounded maxsize a drop metrikou."""
    maxsize: int = 256
    _queue: asyncio.Queue[T_out] = field(default_factory=lambda: asyncio.Queue(maxsize=0))
    _drop_count: int = 0

    def __post_init__(self):
        object.__setattr__(self, "_queue", asyncio.Queue(maxsize=self.maxsize))

    async def put(self, item: T_out) -> bool:
        """put_nowait — returns False if full (drop strategy)."""
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self._drop_count += 1
            return False
```

---

## S1-03 — Memory Budget Derived maxsize

Pro fronty vytvořené z memory budget:

```python
# evidence_log.py — S1-03/C3-04 FIX
# asyncio.Queue maxsize derived from memory budget
_memory_budget = psutil.virtual_memory().available * 0.1  # 10% free RAM
ITEM_SIZE_ESTIMATE = 512  # bytes per evidence item
capacity = int(_memory_budget / ITEM_SIZE_ESTIMATE)
capped = min(capacity, 500)  # ceiling at 500 for M1 8GB safety
self._queue = asyncio.Queue(maxsize=capped)
```

---

## S1-04 — Audit Checklist pro nový kód

- [ ] Je `asyncio.Queue` vůbec nutná? (Rust MPSC může být rychlejší)
- [ ] Má fronta explicitní `maxsize`? (`maxsize=0` = unbounded, nutno zdůvodnit)
- [ ] Je `maxsize` odvozen z memory budget nebo pevně konstantní?
- [ ] Existuje `QueueFull` handler?
- [ ] Je `put_nowait` použit v thread contextoch (ne `await queue.put()`)?
- [ ] Je drop-count / backpressure metrika loggeda?

---

## S1-05 — Queue Overflow Strategies

| Strategie | Kdy použít | Implementace |
|-----------|-----------|-------------|
| **Drop oldest** | Real-time streaming, staré = ne relevance | `utils.queue_policy.put_drop_oldest()` |
| **Drop newest** | newest data je nejdůležitější | `utils.queue_policy.put_drop_newest()` |
| **Fail fast** | Kritická data, kde ztráta = selhání | `utils.queue_policy.put_fail_fast()` → `raise QueueFull` |
| **Block** | Synchronous producenti, kde backpressure = správné chování | `await queue.put(item)` (coroutine, jen v async context) |
| **Unbounded** | Jen pro dočasné fallbacky (evidence_log asyncio fallback) | `maxsize=0`, dočasný stav |

---

## S1-06 — Active Queues Map (universal, post-filter)

```
evidence_log.py               →  asyncio.Queue(maxsize=capped)       [memory-budget derived]
evidence_log.py               →  asyncio.Queue[bytes | None]          [async write queue, bounded]
tool_exec_log.py              →  asyncio.Queue(maxsize=MAXSIZE)       [bounded by config]
pipeline/_stage_protocol.py    →  BoundedStageQueue                   [maxsize per stage]
pipeline/finding_pipeline.py   →  asyncio.Queue(maxsize=SIZE)         [bounded]
pipeline/ioc_cooccurrence     →  asyncio.Queue(maxsize=100)           [bounded]
core/sync_bridge.py           →  asyncio.Queue(maxsize=0)             [unbounded intentional]
core/resource_governor.py      →  asyncio.Queue(maxsize=64)           [bounded]
runtime/observability_async_handler.py → asyncio.Queue(maxsize=10_000) [bounded, drop oldest]
layers/communication_layer    →  asyncio.Queue(maxsize=64/256)        [bounded per-subscriber]
utils/two_pass_pipeline.py    →  asyncio.Queue(maxsize=512)           [bounded]
utils/async_utils.py          →  asyncio.Queue(maxsize=MAX*2)        [bounded by concurrency]
utils/jsonl_lz4_writer.py     →  asyncio.Queue(maxsize=MAX)           [bounded]
knowledge/lancedb_store.py    →  asyncio.Queue(maxsize=100)          [per-table bounded]
knowledge/pipelined_ingestor  →  asyncio.Queue(maxsize=2)            [cross-batch]
knowledge/analytics_hook.py    →  asyncio.Queue(maxsize=200)          [bounded]
knowledge/graph_rag.py        →  asyncio.Queue(maxsize=10)           [bounded]
prefetch/prefetch_cache.py     →  asyncio.Queue(maxsize=maxsize)      [runtime bounded]
prefetch/prefetch_pipeline.py  →  asyncio.Queue(maxsize=50)           [bounded]
brain/continuous_batch_engine  →  asyncio.Queue(maxsize=128)          [bounded]
brain/ner_engine.py           →  asyncio.Queue(maxsize=16)           [bounded]
transport/nym_transport.py    →  asyncio.Queue(maxsize=MAX)          [runtime bounded]
research/parallel_scheduler.py →  asyncio.PriorityQueue()             [thread-safe internal]
coordinators/fetch_coordinator.py →  (catch asyncio.QueueFull)       [no own queue — caller-side]
```

**Total active bounded queues:** ~26
**Total unbounded (legitimate):** ~4 (sync_bridge intentional, stage_protocol init-hack, observability default-0, evidence_log Rust fallback)
