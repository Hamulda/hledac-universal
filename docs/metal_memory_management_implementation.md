# Metal Memory Management - F265-METAL Implementation

## Datum: 2026-06-23

## Nové API v `utils/mlx_memory.py`

### 1. `set_default_memory(buffer_mb: int = 512) -> dict`

Nastaví default Metal memory buffer size.

```python
from utils.mlx_memory import set_default_memory

result = set_default_memory(512)
# result: {"success": True, "buffer_mb": 512, "error": None}
```

**Parametry:**
- `buffer_mb`: Velikost bufferu v MB (default: 512, rozsah: 128-1536)

**Returns:** `dict` s klíči:
- `success`: bool - úspěch operace
- `buffer_mb`: int - skutečná velikost bufferu (clamped)
- `error`: str | None - chybová zpráva

**Safety:** Automatically clamped to [128, 1536] MiB for M1 8GB stability.

---

### 2. `get_memory_info() -> dict`

Komplexní Metal memory status.

```python
from utils.mlx_memory import get_memory_info

info = get_memory_info()
# info: {"used": 0, "peak": 0, "cache": 0, "available": True, "pressure": "NORMAL", "pressure_pct": 0}
```

**Returns:** `dict` s klíči:
- `used`: int - aktivní paměť v bytes
- `peak`: int - peak paměť v bytes
- `cache`: int - cache paměť v bytes
- `available`: bool - MLX dostupnost
- `pressure`: str - NORMAL|WARNING|CRITICAL|UNKNOWN
- `pressure_pct`: int - procentuální využití

---

### 3. `MetalPreallocator` Třída

Tier-based Metal memory pre-allocator.

```python
from utils.mlx_memory import MetalPreallocator

pa = MetalPreallocator(default_tier="medium")

# Aplikovat tier
result = pa.apply_tier("high")
# result: {"tier": "high", "success": True, "buffer_mb": 384, "cache_mb": 512, "wired_mb": 768, "errors": []}

# Získat status
status = pa.get_status()
# status: {"tier": "high", "configured": True, "memory_info": {...}, "tier_config": {...}}

# Adaptivní update dle aktuální paměti
adaptive = pa.adaptive_update()
```

---

### 4. Memory Tiers

Tier-based konfigurace pro M1 8GB:

| Tier | Buffer (MB) | Cache (MB) | Wired (MB) |
|------|-------------|------------|-------------|
| idle | 768 | 1024 | 1536 |
| low | 640 | 896 | 1280 |
| medium | 512 | 768 | 1024 |
| high | 384 | 512 | 768 |
| critical | 256 | 384 | 512 |
| emergency | 128 | 256 | 384 |

---

### 5. Integrační Body

#### Do `brain/deephermes3_engine.py`:

```python
from utils.mlx_memory import MetalPreallocator, get_memory_info

# V __init__:
self._preallocator = MetalPreallocator(default_tier="medium")

# V initialize() - po model load:
config = self._preallocator.apply_tier("medium")

# V generate() - adaptivní monitoring:
if should_adapt_memory():
    self._preallocator.adaptive_update()
```

#### Do `core/resource_governor.py`:

```python
from utils.mlx_memory import recommend_tier_config

# Při CRITICAL stavu:
config = recommend_tier_config(uma_state="critical")
```

---

## Invariants (Testované)

| ID | Invariant | Popis |
|----|-----------|-------|
| INV-1 | `mx.eval([])` před `mx.clear_cache()` | Vždy flush lazy ops |
| INV-2 | Buffer clamped [128, 1536] MiB | M1 8GB safety |
| INV-3 | Fail-safe návratové hodnoty | Nikdy neraises |
| INV-4 | Lazy MLX import | Žádný import při boot |

---

## User Snippet Kompatibilita

Původní user snippet:

```python
import mlx.core as mx

mx.set_default_memory(512 * 1024 * 1024)  # 512MB

status = mx.get_memory_info()
print(f"Used: {status['used'] / 1e9:.2f}GB")
```

Nyní funguje přes wrappery:

```python
from utils.mlx_memory import set_default_memory, get_memory_info

set_default_memory(512)  # 512MB
info = get_memory_info()
print(f"Used: {info['used'] / 1e9:.2f}GB")
```

---

*Generated: 2026-06-23 | Sprint: F265-METAL*
