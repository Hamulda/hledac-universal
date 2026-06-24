# Metal Memory Management Analysis - M1 8GB UMA
## Sprint F265-METAL | Datum: 2026-06-23

---

## 1. SOUČASNÝ STAV (Audit)

### 1.1 API Využití Napříč Kódem

| API | mlx_memory.py | deephermes3_engine.py | mlx_cache.py | Status |
|-----|---------------|----------------------|---------------|--------|
| `get_active_memory` | 11× | 32× | 0× | ✅ Aktivně používáno |
| `get_peak_memory` | 11× | 0× | 0× | ✅ Aktivně používáno |
| `get_cache_memory` | 11× | 0× | 0× | ✅ Aktivně používáno |
| `clear_cache` | 16× | 10× | 0× | ✅ Moderní API (`mx.clear_cache`) |
| `mx.metal.clear_cache` | ⚠️ 4× deprecated | ⚠️ 6× deprecated | 0× | ⚠️ Zastaralé, migrváno postupně |
| `set_cache_limit` | 9× | 0× | 26× | ✅ Aktivně používáno |
| `set_wired_limit` | 0× | 0× | 10× | ✅ Aktivně používáno |
| `set_memory_limit` | 2× | 0× | 0× | ⚠️ Nemusí existovat v MLX |
| **`set_default_memory`** | **0×** | **0×** | **0×** | ❌ **CHYBÍ - User API** |
| **`get_memory_info`** | **0×** | **0×** | **0×** | ❌ **CHYBÍ - User API** |

### 1.2 Metal Memory Tier Thresholds (mlx_cache.py)

```python
_METAL_CACHE_LIMIT_BYTES = 1.5 GiB   # Ceiling pro M1 8GB
_METAL_WIRED_LIMIT_BYTES = 1.5 GiB    # Pinned Metal memory

# Dynamic sizing formula (get_dynamic_metal_cache_limit):
# min(max(available * 0.2, 512 MiB), 1.5 GiB)
# - Normal floor: 512 MiB
# - EMERGENCY floor: 256 MiB  
# - Ceiling: 1.5 GiB
```

### 1.3 Identifikované Problémy

#### P0 - Kritické
1. **Žádné `set_default_memory` volání** - MLX API z user snippet není implementováno
2. **Žádné `get_memory_info` volání** - chybí komplexní memory status API
3. **Deprecated `mx.metal.clear_cache`** - 6× v deephermes3_engine.py

#### P1 - Vysoká Priorita  
4. **Duplicitní Metal memory monitoring** - 3 různé moduly volají podobné API
5. **Žádná unified pre-allocate strategie** - Metal buffery alokovány on-demand
6. **Chybí `mx.eval([])` před všemi `clear_cache`** - některá volání mohou být neefektivní

---

## 2. USER SNIPPET ANALYZA

```python
# User's proposed Metal memory management
import mlx.core as mx

# Pre-allocate Metal buffers
mx.set_default_memory(512 * 1024 * 1024)  # 512MB default

# Monitor memory
status = mx.get_memory_info()
print(f"Used: {status['used'] / 1e9:.2f}GB")
```

### 2.1 API Existance Check

| API | Existence | MLX Verze | Purpose |
|-----|-----------|-----------|---------|
| `mx.set_default_memory(bytes)` | ❓ Neověřeno | ? | Nastaví default memory limit |
| `mx.get_memory_info()` | ❓ Neověřeno | ? | Vrátí komplexní memory info |

### 2.2 Bezpečnostní Analýza

**Rizika při nasazení `set_default_memory`:**
- M1 8GB UMA má **6.25 GiB** max celkový budget
- 512 MiB default = **8%** celkové paměti
- Možnost undershootu → Metal musí častěji alokovat/dealokovat
- Nutnost dynamické adaptace na aktuální stav systému

---

## 3. Doporučené ŘEŠENÍ

### 3.1 Architecture: Unified Metal Memory Manager

```
┌─────────────────────────────────────────────────────────────┐
│                  METAL MEMORY GOVERNANCE                     │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ mlx_memory   │  │ mlx_cache    │  │ deephermes3  │   │
│  │ _memory.py   │  │ _cache.py    │  │ _engine.py   │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                  │                  │            │
│         └──────────────────┼──────────────────┘            │
│                            ▼                               │
│              ┌─────────────────────────┐                   │
│              │  UnifiedMetalGovernor  │                   │
│              │  (NEW - mlx_memory.py) │                   │
│              └─────────────────────────┘                   │
│                            │                               │
│         ┌──────────────────┼──────────────────┐            │
│         ▼                  ▼                  ▼            │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐       │
│  │ Pre-alloc │    │ Monitoring │    │  Cleanup   │       │
│  │ Manager   │    │ & Tiers   │    │  Orchestr. │       │
│  └────────────┘    └────────────┘    └────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Nové API Funkce

#### A. Pre-Allocation Manager
```python
class MetalPreallocator:
    """Správce pre-alokace Metal bufferů."""
    
    DEFAULT_BUFFER_MB = 512  # User snippet value
    MIN_BUFFER_MB = 256
    MAX_BUFFER_MB = 1536    # 1.5 GiB ceiling
    
    def set_default_memory(self, mb: int = 512) -> bool:
        """Nastaví default Metal memory buffer."""
        
    def get_memory_info(self) -> dict:
        """Komplexní memory info (user snippet API)."""
        
    def recommend_buffer_size(self) -> int:
        """Doporučí optimální velikost bufferu dle aktuálního stavu."""
```

#### B. Dynamic Tier-Based Pre-Allocation
```python
# Metal memory tier-based pre-allocation (M1 8GB optimized)
TIERS = {
    'idle':         {'buffer_mb': 768,  'cache_mb': 1024},
    'low':          {'buffer_mb': 512,  'cache_mb': 768},
    'medium':       {'buffer_mb': 384,  'cache_mb': 512},
    'high':         {'buffer_mb': 256,  'cache_mb': 384},
    'critical':     {'buffer_mb': 128,  'cache_mb': 256},
}
```

### 3.3 Implementační Kroky

| Krok | Soubor | Akce | Priority |
|------|--------|------|----------|
| 1 | mlx_memory.py | Přidat `set_default_memory` wrapper s validací | P0 |
| 2 | mlx_memory.py | Přidat `get_memory_info` komplexní status | P0 |
| 3 | mlx_memory.py | Vytvořit `MetalPreallocator` třídu | P1 |
| 4 | deephermes3_engine.py | Migrace `mx.metal.clear_cache` → `mx.clear_cache` | P1 |
| 5 | mlx_cache.py | Integrace s new preallocator | P2 |
| 6 | Testy | Přidat probe testy pro nové API | P2 |

---

## 4. Cutting-Edge Metody (Pro M1 8GB)

### 4.1 Adaptive Memory Budget
```python
# Dynamický rozpočet Metal paměti dle UMA stavu
def compute_metal_budget(uma_state: str) -> dict:
    """M1 8GB adaptive Metal memory budget."""
    budgets = {
        'ok':         {'buffer': 768,  'cache': 1024, 'wired': 1536},
        'soft_warn':  {'buffer': 640,  'cache': 896,  'wired': 1280},
        'warn':       {'buffer': 512,  'cache': 768,  'wired': 1024},
        'critical':   {'buffer': 384,  'cache': 512,  'wired': 768},
        'emergency':  {'buffer': 256,  'cache': 384,  'wired': 512},
    }
    return budgets.get(uma_state, budgets['ok'])
```

### 4.2 Proactive Memory Pressure Detection
```python
# Prediktivní memory pressure detection
def predict_memory_pressure() -> str:
    """Predikuje memory pressure před jeho nastanem."""
    active = mx.get_active_memory()
    cache = mx.get_cache_memory() if hasattr(mx, 'get_cache_memory') else 0
    
    # Koeficient růstu
    growth_rate = (active - last_active) / time_delta if last_active else 0
    
    if growth_rate > CRITICAL_GROWTH_RATE:
        return 'critical'  # Brzdi hned
    elif growth_rate > WARNING_GROWTH_RATE:
        return 'warn'
    return 'ok'
```

### 4.3 M1 8GB Optimální Memory Layout
```
┌─────────────────────────────────────────────┐
│           M1 8GB UNIFIED MEMORY            │
├─────────────────────────────────────────────┤
│  macOS Baseline      ~2.5 GiB               │
│  Python/App         ~1.0 GiB               │
│  MLX Model (4bit)   ~2.0 GiB              │
│  KV Cache           ~0.75 GiB             │
│  Metal Buffer       ~0.5-1.0 GiB  ← NEW  │
│  Metal Cache        ~0.5-1.0 GiB          │
├─────────────────────────────────────────────┤
│  VOLNÉ              ~0.5-1.0 GiB           │
└─────────────────────────────────────────────┘
```

---

## 5. INVARIANTS (Povinné Pro Všechny Změny)

| ID | Invariant | Test |
|----|-----------|------|
| INV-1 | `mx.eval([])` **vždy** před `mx.clear_cache()` | `test_eval_before_clear_cache` |
| INV-2 | `set_default_memory` max **1536 MiB** na M1 8GB | `test_default_memory_ceiling` |
| INV-3 | `set_cache_limit` voláno pouze v `_ensure_metal_memory_limits` | `test_cache_limit_centralized` |
| INV-4 | Žádné `mx.metal.clear_cache` v novém kódu | `test_no_deprecated_api` |
| INV-5 | Memory monitoring vždy s fail-safe fallback | `test_memory_monitoring_failsafe` |

---

## 6. AKČNÍ PLÁN

### Fáze 1: Core Infrastructure (P0)
- [ ] Přidat `set_default_memory` wrapper do `mlx_memory.py`
- [ ] Přidat `get_memory_info` API wrapper
- [ ] Migrace deprecated `mx.metal.clear_cache` → `mx.clear_cache`

### Fáze 2: Pre-Allocation Engine (P1)
- [ ] Vytvořit `MetalPreallocator` třídu
- [ ] Implementovat tier-based dynamickou alokaci
- [ ] Proaktivní memory pressure detection

### Fáze 3: Integration & Testing (P2)
- [ ] Wire preallocator do `deephermes3_engine.py`
- [ ] Přidat probe testy
- [ ] Dokumentace nových API

---

*Generated: 2026-06-23 | Sprint: F265-METAL*
