


## NEW Pattern #17: Inference Backend Duplication (core/)

**pyscn data:**
- `mlxcel_backend.py` ↔ `inference_coordinator.py`: 5 pairs
- `coreml_backend.py` ↔ `inference_coordinator.py`: 5 pairs
- `coreml_backend.py` ↔ `mlxcel_backend.py`: 2 pairs

### Root cause

Backend-agnostic inference API (`inference_coordinator.py`) definuje společné patterns které jsou pak **ručně kopírovány** do každého backendu místo dědění.

### Actionable fix

Vytvořit `core/inference_backends/_base.py`:
```python
class BaseInferenceBackend(ABC):
    async def load_model(self, path: Path) -> ModelHandle: ...
    async def generate(self, prompt: str, **kwargs) -> GenerationResult: ...
    def get_cache_stats(self) -> CacheStats: ...
```

**Estimated savings:** ~12 clone pairs.

---

## NEW Pattern #18: Frameworks Init PEP 562 Pollution (core/)

**pyscn data:** `core/frameworks/__init__.py` = 15 pairs (druhá nejvyšší v core/)

### Root cause

`frameworks/__init__.py` používá `__getattr__` pro lazy loading, ale definice jsou téměř identické mezi různými frameworky.

### Actionable fix

Společný `LazyFrameworkLoader`:
```python
class LazyFrameworkLoader:
    def __init__(self, framework_name: str, entry_points: dict[str, str]): ...
    def __getattr__(self, name: str): ...
```

---

## Cross-Domain Clone Analysis

### Mezi knowledge/ ↔ runtime/

Nalezeno **0 cross-domain párů** mezi knowledge/ a runtime/ v pyscn datech.
To znamená žeStorage (DuckDB) a Execution (Scheduler) jsou čistě oddělené — žádný přímý kód sdílený.

### Mezi core/ ↔ runtime/

Nalezeno **0 cross-domain párů** mezi core/ a runtime/.
To znamená že infrastructure (core) a orchestration (runtime) jsou také čistě oddělené.

**Interpretace:** Clone problém je **intra-doménový**, ne inter-doménový. Každá doména má své vlastní problémy s duplikací.

---


