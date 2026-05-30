# SIDECAR_PROTOCOL_REPORT.md

## F350M-R: Protocol-Based Sidecar Registry

**Datum:** 2026-05-30
**Status:** IMPLEMENTED

---

## Executive Summary

Implemented Protocol-based plugin registry for sidecar adapters, replacing hardcoded `DEFAULT_SIDECAR_RUNNERS` list with dynamic discovery. Five orphaned sidecar modules converted to the new protocol.

---

## Architecture

### Before (Hardcoded)

```python
# runtime/sidecar_bus.py
DEFAULT_SIDECAR_RUNNERS: list[tuple[str, SidecarRunner]] = [
    ("leak_sentinel", _leak_sentinel_runner),
    ("fediverse", _fediverse_runner),
    ...
]
```

### After (Protocol-Based)

```python
# runtime/sidecar_protocol.py
@runtime_checkable
class SidecarAdapterProtocol(Protocol):
    sidecar_id: str
    env_gate: str
    ram_budget_mb: int
    priority: int

    async def run(self, ctx: SidecarContext) -> list[Any]: ...
    def is_available(self) -> bool: ...

# runtime/sidecar_protocol_adapters.py
@SidecarRegistry.register("fediverse")
class FediverseSidecarAdapter(BaseSidecarAdapter):
    sidecar_id: str = "fediverse"
    env_gate: str = "HLEDAC_ENABLE_FEDIVERSE"
    ram_budget_mb: int = 50
    priority: int = 6
```

---

## New Files

| File | Purpose |
|------|---------|
| `runtime/sidecar_protocol.py` | Protocol definitions + SidecarRegistry |
| `runtime/sidecar_protocol_adapters.py` | 5 protocol adapters |

---

## Registered Adapters

| Sidecar ID | Env Gate | RAM (MB) | Priority | Source Module |
|------------|----------|----------|----------|---------------|
| fediverse | HLEDAC_ENABLE_FEDIVERSE | 50 | 6 | discovery/fediverse_adapter.py |
| dht | HLEDAC_ENABLE_DHT | 100 | 4 | discovery/dht_adapter.py |
| academic | HLEDAC_ENABLE_ACADEMIC | 80 | 5 | discovery/academic/__init__.py |
| alt_protocols | HLEDAC_ENABLE_ALT_PROTOCOLS | 60 | 4 | fetching/alternative_protocol_fetcher.py |
| leak_sentinel | HLEDAC_ENABLE_LEAKSENTINEL | 30 | 3 | intelligence/leak_sentinel.py |

---

## SidecarContext Dataclass

```python
@dataclass
class SidecarContext:
    query: str                    # Original sprint query
    sprint_id: str                # Sprint identifier
    findings: list[Any]           # Accepted CanonicalFinding objects
    sprint_mode: str              # aggressive/active/passive/research
    memory_pressure: float = 0.0  # RSS/max_rss ratio (0.0-1.0)
```

---

## SidecarRegistry API

```python
class SidecarRegistry:
    @classmethod
    def register(cls, sidecar_id: str) -> Callable[[type], type]:
        """Decorator to register a sidecar adapter."""

    @classmethod
    def get_available(cls, memory_budget_mb: int) -> list[SidecarAdapterProtocol]:
        """Return available sidecars sorted by priority."""

    @classmethod
    def get(cls, sidecar_id: str) -> type | None:
        """Get a registered sidecar class by ID."""

    @classmethod
    def get_all_registered(cls) -> list[str]:
        """Return all registered sidecar IDs."""
```

---

## How to Add a New Sidecar

### 3 Steps:

1. **Implement SidecarAdapterProtocol**
   ```python
   @SidecarRegistry.register("my_sidecar")
   class MySidecarAdapter(BaseSidecarAdapter):
       sidecar_id: str = "my_sidecar"
       env_gate: str = "HLEDAC_ENABLE_MY_SIDECAR"
       ram_budget_mb: int = 50
       priority: int = 5

       async def run_async(self, ctx: SidecarContext) -> list[Any]:
           # Your logic here
           return findings
   ```

2. **Set environment gate**
   ```bash
   export HLEDAC_ENABLE_MY_SIDECAR=1
   ```

3. **Done!** Registry auto-discovers at runtime.

---

## Integration with SprintScheduler

Replace hardcoded list with registry query:

```python
# In sprint_scheduler.py (future enhancement)
from runtime.sidecar_protocol import SidecarRegistry

# Get available sidecars based on memory budget
available = SidecarRegistry.get_available(memory_budget_mb=remaining_ram_mb)

# Run in priority order
for sidecar in available:
    results = await sidecar.run(ctx)
    await duckdb_store.async_ingest_findings_batch(results)
```

---

## GHOST_INVARIANTS

- **Fail-safe:** All `run()` methods wrapped in try/except
- **Bounded:** `ram_budget_mb` always checked before execution
- **No blocking ops:** All I/O is async
- **Runtime type checking:** `@runtime_checkable` on Protocol

---

## Migration Path

1. **Phase 1 (DONE):** Protocol + Registry + 5 adapters
2. **Phase 2 (TODO):** Update `sidecar_bus.py` to use registry
3. **Phase 3 (TODO):** Convert remaining hardcoded runners

---

## Notes

- Existing `DEFAULT_SIDECAR_RUNNERS` still works (backward compatible)
- Protocol adapters are additive, not replacements
- Memory budget checks are advisory (caller enforces)
- Priority range: 1-10 (higher = runs first)
