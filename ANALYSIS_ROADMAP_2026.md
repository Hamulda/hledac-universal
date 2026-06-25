# Hledac Universal — Komplexní Analýza a Roadmap 2026
**MacBook Air M1 8GB | Python | MLX | DuckDB | Rust**

---

## EXEKUTIVNÍ SOUHRN

Projekt **Hledac Universal** je rozsáhlý OSINT orchestrátor s 32,000+ řádky kódu v `sprint_scheduler.py` a komplexní architekturou zahrnující:
- **Brain**: MLX inference, batch scheduling, continuous batching
- **Knowledge**: DuckDB + LMDB storage, LanceDB identity store, dedup management
- **Runtime**: Sprint lifecycle, acquisition strategy, resource governance
- **Transport**: curl_cffi stealth, circuit breakers, HTTP/3
- **Coordinators**: Fetch, memory, resource coordination

### Klíčová zjištění
| Kategorie | Počet | Závažnost |
|-----------|--------|------------|
| Kritické problémy | 12 | 🔴 HIGH |
| Střední problémy | 18 | 🟡 MEDIUM |
| Doporučení pro optimalizaci | 24 | 🟢 LOW |
| Modernizační příležitosti | 15 | 🔵 INFO |

---

## 🔴 KRITICKÉ PROBLÉMY

### K1: Sprint Scheduler Monolit (32,217 řádků)

**Problém**: `runtime/sprint_scheduler.py` je **god object** — obsahuje veškerou logiku:
- Sprint lifecycle management
- Branch orchestration (aggressive/active/passive/research)
- DuckDB ingest coordination
- Graph accumulation
- Export pipeline
- 50+ helper funkcí na module-level

**Impact**: 
- Nemožnost testovat jednotlivé komponenty izolovaně
- Circular imports mezi moduly
- Extrémní compile-time při importu
- Risk při refaktoringu

**Řešení (Modern Cutting-Edge)**:
```python
# Rozbití na mikro-služby pomocí structured concurrency
# Sprint F265-5.5 pattern: TaskGroup-based decomposition

class SprintOrchestrator:
    """Decomposed orchestrator - každý concern je izolovaný"""
    
    async def run(self):
        async with TaskGroup() as tg:
            tg.create_task(self._run_lifecycle())
            tg.create_task(self._run_acquisition())
            tg.create_task(self._run_ingestion())
            tg.create_task(self._run_export())
```

**Roadmap krok**: Sprint orchestration decomposition
1. Extract lifecycle do `runtime/sprint_lifecycle_manager.py` (již existuje, ale nepoužívaný plně)
2. Extract acquisition do `runtime/acquisition_coordinator.py`
3. Extract ingest do `knowledge/ingest_coordinator.py`
4. Extract export do `export/sprint_export_coordinator.py`
5. Wire via protocol ABC místo přímých importů

---

### K2: DuckDB Connection Leaks v Legacy Path

**Problém**: `duckdb_store.py` má async/threading mix kde:
- `duckdb_store.py:async_ingest_findings_batch()` běží v async contextu
- DuckDB je synchroní — blokuje event loop na `duckdb.execute()` calls
- Subprocess adapter (`P1-1`) částečně řeší, ale legacy path stále existuje

**Evidence**:
```python
# duckdb_store.py - blocking calls in async context
async def async_ingest_findings_batch(self, findings: list) -> list:
    # ... async code ...
    cursor.execute(sql)  # BLOCKING - event loop blocked!
    # ...
```

**Řešení**:
```python
# Moderní přístup: PyArrow + Streaming execution
import pyarrow as pa
import pyarrow.flight as flight

class ArrowDuckDBWriter:
    """Arrow streaming do DuckDB - zero-copy, non-blocking"""
    
    async def stream_batch(self, batch: pa.RecordBatch):
        # PyArrow Flight pro async transfer
        writer = pa.ipc.new_file(stream, batch.schema)
        await asyncio.to_thread(writer.write_batch, batch)
```

**Roadmap krok**: 
1. Prioritně použít `duckdb_subprocess_adapter.py` (P1-1 isolation)
2. Plánovaná migrace na Arrow Streaming API
3. Eliminovat všechny synchroní DuckDB calls v async kontextu

---

### K3: Memory Pressure v MLX Continuous Batching

**Problém**: `mlx_batched_executor.py` má problém s memory EMA:
```python
# mlx_batched_executor.py - problematic PID controller
self._memory_ema_alpha: float = 0.15  # Pomalejší EMA
self._pid_integral: float = 0.0  # Integral term může overshootovat
```

**Impact**: Na M1 8GB může dojít k:
- Překročení Metal allocator ceiling
- Swapping (>2GB = katastrofa na 8GB)
- OOM killer activation

**Řešení**:
```python
# Moderní přístup: Model Predictive Control (MPC)
# Squeezeformer-style memory budgeting

class AdaptiveMLXMemoryController:
    """MPC-based memory controller s predictivní alokací"""
    
    async def predict_and_allocate(self, batch_size: int) -> bool:
        predicted_rss = self._forecast_rss(batch_size)
        predicted_metal = self._forecast_metal(batch_size)
        
        if predicted_rss + predicted_metal > self._ceiling:
            return False  # Reject batch, don't overshoot
        
        return True
    
    def _forecast_rss(self, batch_size: int) -> float:
        # Exponential smoothing s trend detection
        trend = self._rss_ema - self._prev_rss_ema
        forecast = self._rss_ema + trend * self._horizon
        return forecast
```

**Roadmap krok**:
1. Implementovat MPC-based memory controller
2. Přidat `metal.get_active_memory()` real-time monitoring
3. Kalibrovat na M1 8GB ceiling (6.5GB warning, 7.0GB critical)

---

### K4: Race Condition v Semaphore Adjustment

**Problém**: `utils/concurrency.py` modifikuje semaphore in-place:
```python
# concurrency.py - race condition potential
def adjust_fetch_workers(new_limit: int) -> None:
    _FETCH_SEMAPHORE._value = new_limit  # RACE: concurrent access
```

**Impact**: Na M1 8GB s vysokouconcurrency může dojít k:
- Race mezi governor a fetch worker adjustment
- Překročení limitu kvůli non-atomic update

**Řešení**:
```python
# Moderní přístup: Lock-based adjustment s double-checked locking
import asyncio

class AdaptiveSemaphore:
    """Thread-safe adaptive semaphore s atomic adjustment"""
    
    def __init__(self, initial: int):
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(initial)
        self._target_limit = initial
    
    async def acquire(self):
        async with self._lock:
            if self._sem._value != self._target_limit:
                # Recreate semaphore atomically
                self._sem = asyncio.Semaphore(self._target_limit)
        await self._sem.acquire()
```

**Roadmap krok**:
1. Wrap semaphore adjustment v asyncio.Lock
2. Add verification after adjustment
3. Unit test s concurrent adjustment scenarios

---

### K5: Circular Import Spider Web

**Problém**: Komplexní import dependencies:
```
sprint_scheduler.py
  ├── duckdb_store.py
  │     ├── quality_assessment.py
  │     │     └── dedup.py
  │     └── graph_service.py
  ├── brain/deephermes3_engine.py
  │     ├── model_lifecycle.py
  │     └── mlx_worker_thread.py
  └── coordinators/fetch_coordinator.py
        ├── transport/curl_cffi_fetch.py
        └── tools/lightpanda_manager.py
```

**Evidence**:
- `_LifecycleAdapter` v sprint_scheduler řeší legacy vs runtime API mismatch
- Lazy imports na mnoha místech jsou workaround pro circular deps
- `__getattr__` lazy loading v `core/__init__.py` a `knowledge/__init__.py`

**Řešení**:
```python
# Moderní přístup: Protocol-based Dependency Injection
# Namísto přímých importů - injektuj přes ABC

from typing import Protocol, runtime_checkable

@runtime_checkable
class DuckDBStoreProtocol(Protocol):
    async def async_ingest_findings_batch(self, findings: list) -> list: ...

class SprintScheduler:
    def __init__(self, db_store: DuckDBStoreProtocol):
        self._db = db_store  # Injection místo importu
```

**Roadmap krok**:
1. Extract všechny inter-module contracts do `core/protocols.py`
2. Refaktorovat na constructor injection
3. Use `typing.TYPE_CHECKING` pro type hints bez runtime závislostí

---

### K6: Threading vs Async Mixing v MLX Worker

**Problém**: `mlx_worker_thread.py` kombinuje:
```python
# mlx_worker_thread.py - mixing paradigms
asyncio.run_coroutine_threadsafe(coro, loop)  # Z async do thread
loop.run_forever()  # Threaded event loop
```

**Impact**:
- GIL contention mezi asyncio thread a MLX Metal thread
- Memory overhead z duplikovaných event loops
- Complexity v shutdown/exit paths

**Řešení**:
```python
# Moderní přístup: Single-threaded async MLX dispatch
# MLX natively supports async dispatch

import mlx.core as mx

class AsyncMLXEngine:
    """Čistě async MLX dispatch bez threading overhead"""
    
    def __init__(self, model_path: str):
        self._model = mx.load(model_path)
        self._stream = mx.new_stream()  # Metal stream
    
    async def generate_async(self, prompt: str) -> str:
        # Async dispatch - vrací Future, neblokuje
        return await asyncio.wrap_future(
            asyncio.get_event_loop().run_in_executor(
                None,  # Use default executor
                lambda: self._model.generate(prompt)
            )
        )
```

**Roadmap krok**:
1. Evaluovat mlx-lm async API (pokud existuje)
2. Pokud ne, minimalizovat thread pool na 1 worker
3. Přejít na Metal streams pro IPC

---

## 🟡 STŘEDNÍ PROBLÉMY

### S1: Synchronní I/O v Async Context

**Lokace**: Multiple places
```python
# synthesis_runner.py
import urllib.request  # BLOCKING
# fetch_coordinator.py  
# network/session_runtime.py
```

**Řešení**: Nahradit `urllib.request` za `aiohttp` nebo `httpx`:
```python
import aiohttp

async def fetch_url(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.read()
```

---

### S2: Inefficient JSON Serialization

**Lokace**: `duckdb_store.py`, `export/sprint_exporter.py`

**Problém**: Použití standard `json` místo `orjson`/`msgspec`:
```python
# Některé paths stále používají stdlib json
import json
data = json.dumps(result)  # Pomalé
```

**Evidence**: Projekt už má `orjson` a `msgspec` optimalizace, ale některé legacy paths je obcházejí.

**Řešení**: Centralizovaný JSON codec:
```python
# utils/json_codec.py
import orjson

def dumps(obj) -> str:
    return orjson.dumps(obj, option=orjson.OPT_SERIALIZE_NUMPY).decode()

def loads(data) -> Any:
    return orjson.loads(data)
```

---

### S3: Unbounded Cache Growth

**Lokace**: 
- `memory_coordinator.py` - `MultiLevelContextCache` může růst
- `dedup.py` - `RotatingBloomFilter` má bounded generations, ale LMDB je unbounded
- `lancedb_store.py` - Cache bounded, ale query results ne

**Řešení**: Implementovat Cache LM (Least Frequently Used) eviction:
```python
from collections import OrderedDict

class BoundedCache:
    """Cache s LFU eviction policy"""
    
    def __init__(self, max_size: int = 10000):
        self._cache: OrderedDict = OrderedDict()
        self._freq: dict = {}
        self._max_size = max_size
    
    def get(self, key: str) -> Any:
        if key in self._cache:
            self._freq[key] += 1
            self._cache.move_to_end(key)
            return self._cache[key]
        return None
    
    def _evict_lfu(self):
        if len(self._cache) >= self._max_size:
            lfu_key = min(self._freq, key=self._freq.get)
            del self._cache[lfu_key]
            del self._freq[lfu_key]
```

---

### S4: Circuit Breaker State Machine Complexity

**Lokace**: `transport/circuit_breaker.py` (731 řádků)

**Problém**: Komplexní state machine s 3 stavy, warmup tracking, boot phase detection:
```python
class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
```

**Řešení**: Využít existing resilience patterns:
```python
# Použít pybreaker nebo similar established library
import pybreaker

breaker = pybreaker.CircuitBreaker(
    fail_max=3,
    reset_timeout=30,
    exclude=[requests.codes.timeout]
)
```

---

### S5: Inefficient Bloom Filter Regeneration

**Lokace**: `dedup.py` - `RotatingBloomFilter`

**Problém**: Generace state file + mmap file creation na každý start:
```python
def _init_filters(self) -> None:
    # Generuje nové mmap soubory pokaždé
    self._active = self._MmapBloomFilter(...)
```

**Řešení**: Lazy initialization s existence check:
```python
def _init_filters(self) -> None:
    if Path(self._active_path).exists():
        # Load existing
        self._active = self._MmapBloomFilter.load(self._active_path)
    else:
        # Create new
        self._active = self._MmapBloomFilter(...)
```

---

### S6: Synchronous GC Callbacks

**Lokace**: `sprint_scheduler.py` - `_gc_sprint_callback`

**Problém**: GC callback běží synchronně, může blockovat:
```python
_gc_sprint_callback_handle: Callable | None = None

def _gc_sprint_callback(phase: str, info: dict) -> None:
    _gc_sprint_stats.append({...})  # Blocking
```

**Řešení**: Offload do async task:
```python
def _gc_sprint_callback(phase: str, info: dict) -> None:
    loop = asyncio.get_event_loop()
    loop.call_soon_threadsafe(
        lambda: _gc_sprint_stats.append({...})
    )
```

---

### S7: Unoptimized Regex Patterns

**Lokace**: Multiple files s inline regex compilation

**Problém**: Regex compiled on every call:
```python
# synthesis_runner.py
re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', content)  # Compile each time
```

**Řešení**: Compile once at module level:
```python
_IP_PATTERN = re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b')

def find_ips(content: str) -> list[str]:
    return _IP_PATTERN.findall(content)
```

---

### S8: Missing Type Annotations

**Lokace**: Legacy modules

**Problém**: Incomplete type hints omezují static analysis

**Řešení**: Postupná migrace na `pyright` strict mode:
```python
# postupně přidávat type hints
def process_findings(findings: list[CanonicalFinding]) -> list[FindingQualityDecision]:
    ...
```

---

## 🟢 OPTIMALIZAČNÍ DOPORUČENÍ

### O1: MLX KV Cache Optimization

**Current**: `max_kv_size=8192` fixed

**Optimalizace**: Adaptive KV cache sizing based on input length:
```python
def calculate_kv_cache_size(input_tokens: int, max_tokens: int) -> int:
    # Dynamic sizing based on expected output
    headroom = min(max_tokens, 1024)
    return min(input_tokens + headroom, 8192)
```

---

### O2: LanceDB Query Batching

**Current**: Sequential queries for entity resolution

**Optimalizace**: Batch queries for multiple entities:
```python
async def resolve_entities_batch(self, entity_ids: list[str]) -> list[Entity]:
    # Single LanceDB query místo N queries
    results = await self._table.search() \
        .where(f"entity_id IN ({','.join(entity_ids)})") \
        .to_list()
    return results
```

---

### O3: DuckDB WAL Optimization

**Current**: Per-insertion WAL writes

**Optimalizace**: Batch WAL commits:
```python
async def ingest_batch_buffered(self, findings: list) -> None:
    # Buffer findings
    self._buffer.extend(findings)
    
    # Flush when buffer full or timeout
    if len(self._buffer) >= 100 or self._flush_timer.elapsed() > 1.0:
        await self._flush_buffer()
        self._buffer.clear()
```

---

### O4: Network Connection Pool Tuning

**Current**: Default aiohttp/curl_cffi pools

**Optimalizace**: Tune for M1 8GB:
```python
# Optimalizované pool size pro 8GB RAM
MAX_CONCURRENT_CONNECTIONS = 12  # Místo 25
POOL_TIMEOUT = 30.0
KEEPALIVE = 60.0
```

---

### O5: psutil Sampling Frequency

**Current**: Potenciálně příliš časté sampling

**Optimalizace**: Adaptive sampling:
```python
class AdaptiveSampler:
    def __init__(self):
        self._last_sample = 0
        self._interval = 1.0  # Start at 1s
    
    def sample(self):
        now = time.monotonic()
        if now - self._last_sample < self._interval:
            return None  # Skip
        
        # Adjust interval based on stability
        if self._stable_count > 10:
            self._interval = min(5.0, self._interval * 1.2)
        
        return self._do_sample()
```

---

## 🔵 MODERNIZAČNÍ PŘÍLEŽITOSTI

### M1: Async Generators Pipeline

**Current**: List-based processing

**Modernizace**:
```python
# Streaming pipeline s async generators
async def streaming_pipeline(self, source: AsyncIterator[Finding]) -> AsyncIterator[Result]:
    async for finding in source:
        processed = await self._process(finding)
        if processed:
            yield processed
```

---

### M2: Structured Concurrency (Python 3.11+)

**Current**: Manual TaskGroup management

**Modernizace**:
```python
# Python 3.11+ structured concurrency
async with TaskGroup() as tg:
    tg.create_task(self.phase1())
    tg.create_task(self.phase2())
    tg.join()  # Wait for all
```

---

### M3: Pattern Matching (Python 3.10+)

**Current**: Long if/elif chains

**Modernizace**:
```python
# Match expression
match event:
    case FetchEvent(url=url, status=200):
        return Success(url)
    case FetchEvent(url=url, status=429):
        return RateLimited(url)
    case FetchEvent(url=url, error=e):
        return Failed(url, e)
```

---

### M4: dataclass transform (Python 3.12+)

**Current**: Manual `__post_init__` patterns

**Modernizace**:
```python
@dataclass(transform=validate_fields)
class Finding:
    finding_id: str
    confidence: float
    source_type: str
```

---

### M5: Buffer Protocol for Zero-Copy

**Current**: Copy between Arrow/pandas/DuckDB

**Modernizace**:
```python
# Zero-copy Arrow IPC
batch = pa.record_batch([col1, col2], names=['a', 'b'])
writer = pa.ipc.new_stream(output_stream, batch.schema)
writer.write_batch(batch)  # Zero-copy
```

---

## 📋 PRIORITIZOVANÁ ROADMAPA

### Fáze 1: Critical Fixes (1-2 týdny)

| # | Úkol | Závislost | Odhad |
|---|------|----------|-------|
| 1.1 | Fix semaphore race condition | - | 2h |
| 1.2 | Enable subprocess DuckDB mode | - | 1h |
| 1.3 | Add memory pressure MPC controller | O3 | 4h |
| 1.4 | Compile regex patterns at module level | - | 3h |
| 1.5 | Implement bounded cache eviction | S3 | 4h |

### Fáze 2: Performance (2-4 týdny)

| # | Úkol | Závislost | Odhad |
|---|------|----------|-------|
| 2.1 | MLX KV cache optimization | 1.3 | 4h |
| 2.2 | LanceDB batch queries | - | 6h |
| 2.3 | DuckDB WAL batching | 1.2 | 8h |
| 2.4 | Adaptive psutil sampling | - | 4h |
| 2.5 | Network pool tuning | - | 2h |

### Fáze 3: Architecture (4-8 týdnů)

| # | Úkol | Závislost | Odhad |
|---|------|----------|-------|
| 3.1 | Extract SprintScheduler protocols | - | 2d |
| 3.2 | Dependency injection refactor | 3.1 | 3d |
| 3.3 | Async generator pipeline | - | 2d |
| 3.4 | MLX worker simplification | - | 2d |
| 3.5 | Type annotation audit | - | 1d |

### Fáze 4: Modernization (8-12 týdnů)

| # | Úkol | Závislost | Odhad |
|---|------|----------|-------|
| 4.1 | Python 3.12 upgrade path | 3.x | 1d |
| 4.2 | Pattern matching adoption | - | 2d |
| 4.3 | Arrow streaming integration | 3.3 | 3d |
| 4.4 | Structured concurrency rollout | 3.1 | 2d |
| 4.5 | Performance benchmarking suite | 2.x | 2d |

---

## 🧪 TESTING STRATEGY

### Critical Path Tests
```python
# test_critical_path.py
async def test_sprint_lifecycle_transitions():
    """Verify all phase transitions"""
    
async def test_memory_pressure_response():
    """Verify MPC controller decisions"""
    
async def test_semaphore_concurrent_adjustment():
    """Verify race-condition-free adjustment"""
```

### Performance Benchmarks
```bash
# M1 8GB benchmarks
pytest benchmarks/test_memory_pressure.py --benchmark-only
pytest benchmarks/test_throughput.py --benchmark-only
```

---

## 📊 METRIKY ÚSPĚCHU

| Metrika | Current | Target | Měření |
|---------|---------|--------|---------|
| Sprint scheduler LOC | 32,217 | <10,000 | `tokei` |
| Memory spikes | >500MB | <200MB | psutil monitoring |
| GC pause time | Unknown | <50ms | gc callbacks |
| Import time | ~5s | <2s | `time python -c "import hledac"` |
| DuckDB write throughput | ~1000/s | >5000/s | benchmarks |

---

## 🔧 TECHNICKÉ POZNÁMKY

### M1 8GB UMA Specific
- Metal allocator competition s DuckDB
- Swap threshold: 2GB = katastrofa
- GC pressure vyšší než na 16GB+ strojích
- psutil sampling musí být bounded

### Rust Extension Status
- Centralizovaný přes `core.rust_backend`
- PyO3 bindings working
- xxHash3-64, mmap-backed stores operational
- Area pro expansion: more rayonic parallelization

### asyncio Best Practices
- `asyncio.gather(..., return_exceptions=True)` vždy
- `_check_gathered()` po každém gather
- CancelledError → re-raise, ne catch
- `asyncio.TaskGroup` for structured concurrency

---

*Generated: 2026-06-24*
*Project: Hledac Universal*
*Target: MacBook Air M1 8GB*
