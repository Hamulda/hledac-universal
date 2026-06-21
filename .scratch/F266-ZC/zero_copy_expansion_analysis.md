# Zero-Copy Patterns Expansion — F266-ZC

**Sprint:** F266A  
**Datum:** 2026-06-20  
**M1 8GB MacBook Air (UMA)**  
**Status:** ✅ IMPLEMENTOVÁNO

---

## F266A: FetchResult body preservation

### Změny

**`fetching/public_fetcher.py`:**

1. **FetchResult class** — přidán nový field:
```python
body: bytes | None = None  # F266A: raw bytes preserved for Arrow zero-copy
```

2. **httpx success path** (line ~2783):
```python
body=_body.body,  # F266A: raw bytes preserved for Arrow zero-copy
```

3. **aiohttp success path** (line ~3760):
```python
body=body_bytes,  # F266A: raw bytes preserved for Arrow zero-copy
```

4. **curl_cffi success path** (line ~3085):
```python
body=_curl_bytes,  # F266A: raw bytes preserved for Arrow zero-copy
```

### Klíčové vlastnosti

| Property | Value |
|----------|-------|
| Max size | 2MB (bounded by body_limiter max_bytes) |
| Default | `None` (backward compatible) |
| Lazy decode | text is computed from body on-demand |

### Test Results

```
105 passed, 16 warnings in 60.42s
```

---

## Zbývající Příležitosti

### 2. ArrowSharedMemory Arrow IPC path (F266B)
- **Status:** Identifikováno — mrkvý kód
- **Problem:** `serialize()` vždy používá JSON, `_is_arrow_format()` nikdy nevrátí True
- **Oprava:** Implementovat skutečnou Arrow IPC serializaci

### 3. DuckDB body column (F266C)
- **Status:** Připraveno
- **Problem:** `CanonicalFinding` nemá `body` field, pouze `text`
- **Příležitost:** Přidat binary content column do Arrow ingest path

---

## Memory

- `sprint-p04-arrow-ingest.md` — P0-4 Arrow Ingest (2026-06-10)
- `sprint-f265-u5-threadlocal-conn-pool.md` — DuckDB thread_local pool

---

*Updated: 2026-06-20*
