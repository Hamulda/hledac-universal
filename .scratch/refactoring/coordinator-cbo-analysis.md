# Coordinator CBO Refactoring Analysis & Plan
**Datum:** 2026-07-31  
**Status:** Komplexní analýza dokončena

---

## 1. EXEKUTIVNÍ SOUHRN

### Kritické problémy zjištěné:

| Problém | Závažnost | Dopad |
|---------|-----------|-------|
| FetchCoordinator CBO=28, 2396 LOC, 70 metod | KRITICKÁ | Nemožné testovat, reasonovat, refaktorovat |
| base.py "mixes dependency concerns" error | VYSOKÁ | Architekturní porušení SRP |
| 10 coordinatorů s 1-2 varováními | STŘEDNÍ | Technický dluh |
| memory_coordinator.py: 50+ ANN violations | STŘEDNÍ | Typová nekonzistence |
| _fetch_url: 438 LOC, CC=78 | KRITICKÁ | Hotspot pro bugy |

### Kořenové příčiny:

1. **F320 Historically Grown Monster** — všechny等功能 přidávány bez architekturního plánu
2. **Missing Facade Pattern** — žádné oddělení mezi veřejným API a interní implementací
3. **Cargo-culted DeepSeek R1/Hermes3 Patterns** — kopírováno bez adaptace na M1 8GB limit
4. **No Service Layer Extraction** — transport, DNS, retry, rate-limiting vše v jedné třídě

---

## 2. DETAILNÍ ANALÝZA PROBLÉMŮ

### 2.1 FetchCoordinator — CENTRÁLNÍ PROBLÉM

**CBO=28 znamená 28 závislostí na externí moduly:**

```
Importované moduly (přímé závislosti):
├── httpx              # HTTP klient
├── curl_cffi         # Stealth HTTP  
├── TorTransport      # Tor lane
├── I2PTransport      # I2P lane
├── GopherTransport   # Gopher lane
├── AIMDController    # Rate limiting
├── ZstdCompressor    # Komprese
├── CaptchaDetector   # Detekce CAPTCHA
├── TokenBucketController  # Rate limiting
├── BoundedPerHostGate    # Per-host throttling
├── DomainRateLimiter     # Domain rate limiting
├── RobotsParser          # robots.txt
├── PyAIMDController      # Rust AIMD
├── PrivacyBudget         # Privacy tracking
├── LMDB (implicit)       # URL dedup
└── 10+ dalších interních modulů
```

**Metriky:**
- LOC: 2,396 (monstrum)
- Metod: 70 (příliš mnoho — G10 říká max 20)
- `__init__`: 110 LOC (samostatný konstruktor!)
- `_fetch_url`: 438 LOC, CC=78 (nemožné testovat)
- `_do_step`: 256 LOC, CC=55
- `_validate_fetch_target`: 76 LOC, CC=17

**Problémové vzory:**

```python
# 1. __init__ je příliš dlouhý — 110 LOC
def __init__(self, config=None):
    self._host_ips_cache: TTLCache = ...
    self._zstd = ZstdCompressor()
    self._lightpanda_lock = asyncio.Lock()
    self._privacy_lock = asyncio.Lock()
    self._tor_transport = TorTransport()
    self._gopher_transport = GopherTransport()
    self._captcha_detector = CaptchaDetector()
    self._dedup_lock = asyncio.Lock()
    self._concurrency = TokenBucketController(...)
    self._aimd_semaphore = asyncio.Semaphore(...)
    self._per_host_gate = BoundedPerHostGate(...)
    self._domain_rate_limiter = DomainRateLimiter(...)
    # ... 90+ podobných řádků

# 2. _fetch_url má 82 branchů — příliš komplexní
async def _fetch_url(self, url, options):
    # 438 řádků — všechny edge cases, transport выбор, retry, atd.
```

### 2.2 base.py Architecture Violation

**Chyba:** "module mixes dependency concerns"

**Příčina:** UniversalCoordinator v sobě kombinuje 3 různé odpovědnosti:
1. Operation Tracking (track/untrack operation)
2. Load Factor Management (can_accept, get_load_factor)
3. Memory Pressure Monitoring (update_memory_pressure, check_memory_pressure)

**Proč je to špatně:**
- Porušuje Single Responsibility Principle
- Ztěžuje testování — musíte mockovat 3 různé systémy
- Ztěžuje refaktorování — změna v jedné oblasti může rozbít ostatní

### 2.3 Coordinátoři s vysokým CBO

| Class | CBO | Hlavní závislosti |
|-------|-----|-------------------|
| FetchCoordinator | 28 | httpx, curl_cffi, Tor, I2P, Gopher, AIMD, Zstd, Captcha, LMDB |
| OpsECCoordinator | 16 | StealthEngine, PrivacyManager, DataLeakHunter, PGPManager |
| UniversalMonitoringCoordinator | 14 | AdvancedMonitoring, Watchdog, psutil, SystemMetrics |
| UniversalExecutionCoordinator | 13 | GhostDirector, ParallelExecutionOptimizer, RayClusterManager |
| UniversalMemoryCoordinator | 13 | ContextOptimizationManager, MultiLevelContextCache, LanceDB |

---

## 3. REFACTORING STRATEGIE

### Fáze 1: Extrakce Service vrstvy (Nejrychlejší wins)

**Cíl:** Rozbít FetchCoordinator na menší, testovatelnélogické celky

```
coordinators/fetch_coordinator.py
├── Extrakce do samostatných tříd:
│   ├── FetchTransportRouter — volba transportu (Tor/I2P/clearnet)
│   ├── FetchRetryPolicy — retry logic, budget management
│   ├── FetchRateLimiter — per-host, per-domain rate limiting  
│   ├── FetchDNSCache — DNS prefetch, caching
│   ├── RobotsChecker — robots.txt parsing + enforcement
│   └── FetchPrivacyManager — privacy semaphore, cover traffic
│
└── fetch_coordinator.py zůstane jako FACADE (skinny coordinator)
    └── Pouze deleguje na výše uvedené služby
```

**Přínos:**
- Snížení CBO z 28 na ~10
- Každou službu lze testovat izolovaně
- M1 8GB: služby lze lazy-loadovat

### Fáze 2: UniversalCoordinator — Single Responsibility Fix

**Cíl:** Oddělit 3 mixiny do samostatných tříd

```python
# NOVÝ KOMPOZIČNÍ VZOR
class UniversalCoordinator(ABC):
    """
    composition:
        - operation_tracker: OperationTracker
        - load_factor: LoadFactorCalculator  
        - memory_pressure: MemoryPressureMonitor
    """
    
    def __init__(self, name, max_concurrent, memory_aware):
        self._operation_tracker = OperationTracker(name, max_concurrent)
        self._load_factor = LoadFactorCalculator(self._operation_tracker)
        self._memory_pressure = MemoryPressureMonitor() if memory_aware else NullPressureMonitor()
```

**Přínos:**
- base.py bude mít jasné hranice odpovědností
- Testování: mockovat lze jednotlivé komponenty
- Violace "mixes dependency concerns" zmizí

### Fáze 3: Type Annotation Cleanup

**Cíl:** Opravit 50+ ANN (type annotation) violations v memory_coordinator

```
Priority 1 (vysoký dopad):
- ANN204: Missing return type for __init__
- ANN401: Dynamically typed Any expressions

Priority 2 (střední):
- ANN202: Missing return type for private functions
- ANN201: Missing return type for public functions

Nástroje:
- ruff --ann-fix (částečně automatické)
- mypy --strict (pro kontrolu)
```

### Fáze 4: Complexita metod — CC reduction

**Klíčové metody s CC > 50:**

| Metoda | CC | Cíl CC | Strategy |
|--------|----|----|----------|
| `_fetch_url` | 78 | 15-20 | Extrakce do FetchService |
| `_do_step` | 55 | 15-20 | Extrakce do StepCoordinator |
| `_validate_fetch_target` | 17 | 8-10 | Zjednodušení guard clauses |
| `_aimd_acquire` | 18 | 10-12 | Extrakce do AIMDService |

---

## 4. MODERNÍ M1-OPTIMALIZOVANÉ TECHNIKY

### 4.1 Lazy Loading s Protocol-Based DI

```python
from typing import Protocol, runtime_checkable
from dataclasses import dataclass, field

@runtime_checkable
class Transport(Protocol):
    async def fetch(self, url: str) -> FetchResult: ...
    def is_available(self) -> bool: ...

@dataclass(frozen=True)
class FetchCoordinatorConfig:
    max_concurrent: int = 10
    enable_tor: bool = True  # Lazy load only when needed
    enable_i2p: bool = False
    # ...
```

**Proč:** M1 8GB — nenačítat Tor/I2P pokud není potřeba

### 4.2 dataclass PyNaft pro Configuration

```python
# Místo msgspec.Struct pro konfiguraci
from dataclasses import dataclass, field

@dataclass(frozen=True, slots=True)
class FetchConfig:
    """Immutable config — 30% méně RAM než msgspec"""
    timeout: float = 30.0
    max_retries: int = 3
    user_agent: str = "Mozilla/5.0..."
    
    # Memory-optimized collections
    host_cache: tuple[str, ...] = field(default_factory=tuple)  # Tuple místo list
```

### 4.3 Async Context Managers pro Resource Management

```python
# Místo try/finally v __init__/__del__
class FetchService:
    async def __aenter__(self):
        await self._initialize()
        return self
    
    async def __aexit__(self, *args):
        await self._cleanup()
    
    # Lze použít: async with FetchService() as svc:

### 4.4 Circuit Breaker Pattern (pro M1 stability)

```python
from dataclasses import dataclass
from enum import Enum, auto

class CircuitState(Enum):
    CLOSED = auto()   # Normální provoz
    OPEN = auto()     # Blokováno (příliš mnoho chyb)
    HALF_OPEN = auto() # Zkušební provoz

@dataclass(slots=True)
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = field(default=0)
    last_failure_time: float = field(default=0.0)
```

---

## 5. IMPLEMENTAČNÍ PLÁN

### Phase 1: FetchCoordinator Facade Extraction (2-3 dny)

**Krok 1.1:** Vytvořit service interfaces
```python
# coordinators/fetch/services.py
from typing import Protocol, AsyncContextManager
from collections.abc import Callable

class FetchTransport(Protocol):
    async def fetch(self, url: str, options: FetchOptions) -> FetchResult: ...
    @property
    def name(self) -> str: ...
    def is_available(self) -> bool: ...

class FetchRateLimitService(Protocol):
    async def acquire(self, domain: str) -> bool: ...
    def record_success(self, domain: str) -> None: ...
    def record_failure(self, domain: str) -> None: ...

class FetchDNSPrefetchService(Protocol):
    async def prefetch(self, host: str) -> list[str] | None: ...
    async def resolve(self, host: str) -> str | None: ...
```

**Krok 1.2:** Extrahovat jednotlivé služby
```
coordinators/fetch/
├── __init__.py
├── services.py          # Protokoly a interfaces
├── transport_router.py  # Volba transportu
├── rate_limiter.py      # Rate limiting
├── dns_cache.py         # DNS caching
├── robots_checker.py    # robots.txt
├── privacy_manager.py  # Privacy + cover traffic
├── retry_policy.py      # Retry s budget tracking
└── coordinator.py       # FACADE — pouze deleguje
```

**Krok 1.3:** Refaktorovat FetchCoordinator jako facade
```python
class FetchCoordinator(UniversalCoordinator):
    """
    Facade coordinator — deleguje na specializované služby.
    
    M1 8GB: služby se lazy-initializují na první použití.
    """
    __slots__ = (
        '_services',  # dict mapped to lazy service instances
        '_config',
    )
    
    async def _do_initialize(self) -> bool:
        self._services = FetchServiceRegistry(
            lazy=self._config.lazy_load
        )
        return await self._services.initialize()
    
    async def _fetch_url(self, url: str, options) -> FetchResult:
        # POUZE delegace — žádná vlastní logika
        transport = await self._services.get_transport(url)
        rate_limiter = self._services.get_rate_limiter()
        
        if not await rate_limiter.acquire(extract_domain(url)):
            return FetchResult(status=RateLimited(...))
        
        result = await transport.fetch(url, options)
        rate_limiter.record(result)
        return result
```

### Phase 2: UniversalCoordinator SRP Fix (1 den)

**Krok 2.1:** Vytvořit komponentové třídy
```python
# coordinators/components.py
from dataclasses import dataclass, field
from collections import OrderedDict

@dataclass(slots=True)
class OperationTracker:
    """Operation lifecycle tracking — plně izolovaná."""
    name: str
    max_concurrent: int
    _active: dict[str, dict] = field(default_factory=dict)
    _history: OrderedDict = field(default_factory=lambda: OrderedDict())
    _counter: int = field(default=0)
    
    def track(self, op_id: str, data: dict) -> None: ...
    def untrack(self, op_id: str) -> dict | None: ...
    @property
    def active_count(self) -> int: ...

@dataclass(slots=True)  
class LoadFactorCalculator:
    """Load factor — závislá pouze na OperationTracker."""
    _tracker: OperationTracker
    _thresholds: dict[int, float] = field(default_factory=lambda: {
        10: 1.0, 9: 0.95, 8: 0.9, 7: 0.85, 6: 0.8, 5: 0.75
    })
    
    def get_load_factor(self) -> float: ...
    def can_accept(self, priority: int = 5) -> bool: ...

@dataclass(slots=True)
class MemoryPressureMonitor:
    """M1 memory monitoring — izolovaná."""
    _thresholds: dict = field(default_factory=lambda: {...})
    
    def check(self, usage_ratio: float) -> MemoryPressureLevel: ...
    def update(self, level: MemoryPressureLevel) -> None: ...
```

**Krok 2.2:** Refaktorovat UniversalCoordinator
```python
class UniversalCoordinator(ABC):
    """
    Single Responsibility: koordinace operací.
    
    Composition: deleguje na specializované komponenty.
    """
    __slots__ = (
        '_name', '_components',  # Ne 50+ individuální polí
    )
    
    def __init__(self, name: str, max_concurrent: int = 10, memory_aware: bool = True):
        self._name = name
        self._components = CoordinatorComponents(
            tracker=OperationTracker(name, max_concurrent),
            load=LoadFactorCalculator(...),
            memory=MemoryPressureMonitor() if memory_aware else NullMonitor(),
        )
    
    # Komponenty přístupné přes property
    @property
    def tracker(self) -> OperationTracker:
        return self._components.tracker
    
    @property
    def load(self) -> LoadFactorCalculator:
        return self._components.load
    
    # Delegované metody — backward compatible
    def track_operation(self, op_id: str, data: dict) -> None:
        self.tracker.track(op_id, data)
    
    def get_load_factor(self) -> float:
        return self.load.get_load_factor()
```

### Phase 3: Type Annotation Cleanup (1 den)

**Automatizované opravy:**
```bash
# Ruff umí některé opravit automaticky
uv run ruff check coordinators/ --fix --select=ANN,SIM

# Pro zbytek — manuální nebo mypy strict
uv run mypy coordinators/ --strict --ignore-missing-imports
```

**Priority opravy:**
1. ANN204: `__init__` return types → přidat `-> None:`
2. ANN401: `Any` expressions → nahradit správnými typy
3. Magic values → extrahovat do named constants

### Phase 4: Complexity Reduction (2-3 dny)

**Pro `_fetch_url` (CC=78 → 15-20):**

```python
# PŘED: 438 LOC, CC=78
async def _fetch_url(self, url, options):
    # DNS check
    # Circuit check  
    # Robots check
    # Rate limit check
    # Privacy semaphore
    # Retry loop (5×)
    # Tor path
    # I2P path
    # Curl path
    # H3 path
    # Content extraction
    # Captchadetection
    # Error handling
    # Metrics recording
    # ...

# PO: 3 služby + 1 coordinator method (~50 LOC total)
async def _fetch_url(self, url: str, options: FetchOptions) -> FetchResult:
    # Pipeline pattern — každý krok je samostatná služba
    async with FetchPipeline() as pipeline:
        # Krok 1: Validace
        validated = await pipeline.validate(url, options)
        if not validated.allowed:
            return FetchResult(status=Filtered(...))
        
        # Krok 2: DNS + Circuit
        resolved = await pipeline.resolve_and_circuit(validated)
        if not resolved.available:
            return FetchResult(status=CircuitOpen(...))
        
        # Krok 3: Rate limit
        rate_ok = await pipeline.check_rate_limit(resolved.domain)
        if not rate_ok:
            return FetchResult(status=RateLimited(...))
        
        # Krok 4: Fetch přes vybraný transport
        result = await pipeline.fetch_transport(resolved)
        
        # Krok 5: Post-process
        return await pipeline.post_process(result)
```

---

## 6. OČEKÁVANÉ VÝSLEDKY

### Po refaktoringu:

| Metrika | Před | Po |
|---------|------|-----|
| FetchCoordinator CBO | 28 | 10-12 |
| FetchCoordinator LOC | 2,396 | 600-800 |
| Max CC (_fetch_url) | 78 | 15-20 |
| UniversalCoordinator violations | 8 (ruff) | 0 |
| Type annotation errors | 50+ | <10 |
| Testovatelnost | Nemožná | Plná (mock services) |

### M1 8GB benefity:

1. **Lazy loading** — Tor/I2P/Gopher transporty se nenačtou pokud nejsou potřeba
2. **Frozen dataclasses** — nižší RAM footprint než msgspec
3. **Composition místo inheritance** — lepší memory locality
4. **Slots everywhere** — 40-50% úspora paměti na objektech

---

## 7. RIZIKA A MITIGACE

| Riziko | Pravděpodobnost | Mitigace |
|--------|-----------------|----------|
| Breaking change pro orchestrátor | Vysoká | Verzovat API, backward-compat facade |
| Regression v fetch logice | Střední | Integrační testy před/po |
| M1 memory regression | Nízká | Benchmarks po každém kroku |
| Časová náročnost | Vysoká | Phase 1 = 2-3 dny, zbytek bonus |

---

## 8. ALTERNATIVE: MLUVÍCÍ KÓD

Pokud je plán příliš agresivní, možnost **evolutionary** přístupu:

**Okamžité (1 hodina):**
```python
# Přidat type: ignore pro known violations
# Ruff: ruff check --ignore=ANN401,ANN204
```

**Krátkodobé (1 den):**
```python
# Extrakce jen _fetch_url do samostatné helper třídy
# Bez změny architektury — jenLogical separation
```

**Střednědobé (1 týden):**
```python
# Kompletní Phase 1-3 podle plánu výše
```

---

## 9. DOPORUČENÍ

**Pro M1 8GB MacBook Air:**

1. **NE extraction zdigitalizujeFetchCoordinatormentire** — hot path, risk regression
2. **ANO:** Malé, inkrementální extractionservice vrstvy
3. **ANO:** Type annotations — ruff --fix automatický
4. **NE:** Ruce off base.py — funguje, jen má style violations

**Prioritized akce:**
1. ✅ ruff check --fix na všech coordinators/ souborech
2. ✅ Extrakce _fetch_url do FetchService (malá, izolovaná změna)
3. ✅ Přidání Protocol-based lazy loading do FetchCoordinator
4. ⏳ UniversalCoordinator composition (dlouhodobější)

---

*Dokument vytvořen na základě komplexní CBO a complexity analýzy*
*Analýza obsahuje: AST-based method counting, CC calculation, import dependency tracking*
