# Runbook: MLX OOM (Out of Memory)

## Symptoms
- `RuntimeError: metal memory allocation failed`
- `MemoryError: cannot allocate MLX tensor`
- System becomes unresponsive

## M1 8GB Constraints
- RAM budget: ~6.25GB max (macOS 2.5GB + orchestrator 1GB + LLM 2GB + KV cache 0.75GB)
- kv_bits=4 and max_kv_size=8192 are safe settings

## Diagnosis

### 1. Check MLX cache state
```python
import mlx.core as mx
print(f"Cache limit: {mx.metal.get_cache_limit() / 1024**2:.0f}MB")
print(f"Device memory: {mx.metal.device_memory() / 1024**2:.0f}MB")
```

### 2. Check system memory pressure
```python
import psutil
mem = psutil.virtual_memory()
print(f"Used: {mem.used / 1024**3:.1f}GB, Available: {mem.available / 1024**3:.1f}GB")
```

### 3. Check for memory leaks
```bash
# Look for unbounded growth in:
# - resource_allocator.py history (max 100 entries)
# - LMDB pending writes
# - Async task leaks
```

## Emergency Recovery

### 1. Clear MLX cache immediately
```python
import mlx.core as mx
mx.eval([])  # Barrier first
mx.metal.clear_cache()
```

### 2. Reduce cache limit
```python
mx.metal.set_cache_limit(64 * 1024 * 1024)  # 64MB
```

### 3. Call garbage collector
```python
import gc
gc.collect()
```

### 4. Reset LMDB if corrupted
```bash
# Backup first
cp -r data/hledac.lmdb data/hledac.lmdb.bak
# Rebuild if needed
```

## Prevention
- Never call `asyncio.run()` in thread executors (M1 crash vector)
- Always use `mx.eval([])` before `clear_cache()`
- Monitor `high_water_mark` and trigger cleanup at 85%
- Use `--disable-gpu` only if absolutely necessary (slows M1)
- Keep `kv_bits=4` and `max_kv_size=8192` in mlx_lm.generate()

## Safe Memory Thresholds
| Threshold | Action |
|-----------|--------|
| < 70% | Normal operation |
| 70-85% | Warning, monitor closely |
| 85-90% | Block heavy operations, clear cache |
| > 90% | Emergency brake, suspend intake |
