# CLAUDE.md — Hledac Universal OSINT Orchestrator

## CRITICAL INVARIANTS (Top 10)

1. **`asyncio.gather` vždy s `return_exceptions=True`** — `_check_gathered()` po každém gather volání
2. **`mx.eval([])` před `mx.metal.clear_cache()`** — jinak clear_cache je no-op
3. **Žádné `time.sleep()` v async kódu** — používat `asyncio.sleep()` nebo `await asyncio.to_thread()`
4. **Žádné `asyncio.run()` v ThreadPoolExecutor** — M1 crash vector, používat `loop.run_until_complete()`
5. **DuckDB write přes `async_ingest_findings_batch()`** — jediná canonical write path
6. **LMDB bulk write přes `cursor.putmulti()`** — nikdy ne per-item `env.begin(write=True)` v loopu
7. **RotatingBloomFilter pro URL dedup** — nikdy `Set[str]` nebo `ScalableBloomFilter`
8. **M1 Metal cache limit dynamický** — `min(max(available*0.2, 512MiB), 1.5GiB)` ceiling
9. **Fail-safe everywhere** — sidecary vrací `[]` při chybách
10. **Žádné bare `except:`** — vždy `except Exception:` nebo konkrétní typ

## DO NOT (Anti-patterns)

- **Nepřidávej top-level MLX importy** — MLX se importuje lazy, early import crashuje M1
- **Nepoužívej `time.sleep()` v async kódu** — použij `asyncio.sleep()` nebo `await asyncio.to_thread()`
- **Nepiš do DuckDB bez `async_ingest_findings_batch()`** — jediná canonical write path
- **Nepoužívej `asyncio.run()` v ThreadPoolExecutor** — M1 crash, použij `loop.run_until_complete()`
- **Neobcházej `mx.eval([])` před `clear_cache()`** — clear_cache je no-op bez barrier
- **Nepoužívej `ScalableBloomFilter`** — roste bez limitu, nahrazeno `RotatingBloomFilter`
- **Nepiš raw `try/except ImportError` na module level** — použij `utils.optional_imports.optional()` nebo `core.capabilities.CAP`
- **Nepoužívej `bytes()` na LMDB buffer** — ničí zero-copy přenos
- **Nikdy nepřidávej `--disable-gpu` do nodriver args** — na M1 je GPU=CPU, zpomalí to
- **Nevolej `aggressive_cleanup` bez `()`** — musí být `await ...aggressive_cleanup()`

## HARDWARE CONSTRAINTS (M1 8GB UMA)

- **RAM budget:** macOS ~2.5GB + orchestrátor ~1GB + LLM ~2GB + KV cache ~0.75GB = **6.25GB max**
- **Metal cache limit:** 1.5 GiB (1_610_612_736 bytes)
- **KV cache:** `kv_bits=4`, `max_kv_size=8192` v `mlx_lm.generate()`, NE v `load()`
- **SWAP warning:** `relaxed=False` v MLX je feature, ne bug

## KEY SEAMS (Canonical Paths)

| Seam | Path |
|------|------|
| Canonical write | `DuckDBShadowStore.async_ingest_findings_batch()` |
| LMDB metadata | `paths.open_lmdb()` context manager |
| MLX inference | `Hermes3Engine.generate()` |
| HTTP fetch | `FetchCoordinatorAdapter.fetch()` (clearnet→public_fetcher, onion/i2p→FetchCoordinatorFacade) |
| Graph upsert | `DuckPGQGraph.upsert_ioc()` |

## IOC Extraction — Dual Engine

| Engine | Metoda | Kdy použít |
|--------|--------|------------|
| Rust regex `rust.ioc.extract_iocs_flat(text)` | Regex | Clearnet IOC, rychlost |
| Brain NER `brain.ner_engine.extract_iocs_from_text(text)` | ML NER | Volný text, forum, dark web |

`live_public_pipeline.py` volá oba — to je správně.

## DuckDB Lazy Import Anti-Pattern

**Module-level anti-pattern (špatně):**
```python
# ŠPATNĚ — 7µs cold-start penalty:
try:
    from otel import instrumented
except ImportError:
    from hledac.universal.otel._instrumentation import instrumented

# SPRÁVNĚ — zero-cost until first use:
from hledac.universal.utils.optional_imports import optional
_instrumented = optional("otel:instrumented",
    default=optional("hledac.universal.otel._instrumentation:instrumented"))
```

**Allowed:** `except ImportError` inside methods — legitimate runtime deferral.

## PRE-FLIGHT GUARDS (F221-ABORT)

Sprint min duration = `--duration 60s` (30s active window minimum). Abort → `sys.exit(2)`.

Override with `--force` flag → `[F221-FORCED]` warning.

## EXIT CODE CONVENTION (F350M-R)

| Code | Meaning | Trigger |
|------|---------|---------|
| `0` | Clean success | Sprint completed |
| `1` | Runtime error | `except Exception` |
| `2` | Config/validation error | F221-ABORT, argparse, flag-conflict |
| `3` | Programmer error | NameError, AttributeError, ImportError |
| `130` | SIGINT | KeyboardInterrupt |

## Entry Point

```bash
python -m hledac.universal --sprint "QUERY" [--duration SECS] [--aggressive]
```

## Testing

```bash
pytest tests/ -x --timeout=30 -q
```

## Feature Flags

Kanonický zdroj: `core/feature_flags.py` — jediná pravda pro všechny `HLEDAC_ENABLE_*` flags.

Přidat nový flag → přidej do `FeatureFlag` enum v `core/feature_flags.py`.

## Storage Trinity

| Layer | Tech | Purpose |
|-------|------|---------|
| DuckDB | SQL | Canonical findings |
| LMDB | Key-value | Entity/claim metadata, whisper cache |
| LanceDB | ANN | RAG embeddings |

## Optional Dependencies

| Extra | Install | Purpose |
|-------|---------|---------|
| `mlx-embed` | `uv sync --extra mlx-embed` | MLX-native embedding |
| `http3` | `uv sync --extra http3` | Real QUIC via aioquic |

## Before You Edit

Before editing any file, read it first. Before modifying a function, grep for all callers. Research before you edit.

## Common Pitfalls

- `MADV_FREE` (hodnota 5) ≠ `MADV_FREE_REUSABLE` (hodnota 7, Darwin)
- `bytes(v)` na LMDB buffer — ničí zero-copy
- `await self.orch.memory_mgr.aggressive_cleanup` bez `()` — nespustí coroutine
- `--disable-gpu` v nodriver — ZAKÁZÁNO na M1 (GPU=CPU)
