# GRAPH_WIRING_COMPLETE.md — Sprint F214Q Quantum Pathfinder

## Verified Method Signatures

### DuckPGQGraph.find_connected()
```python
# quantum_pathfinder.py:1265
def find_connected(self, value: str, max_hops: int = 2) -> list[dict]:
    """SQL/PGQ MATCH s recursive CTE fallback. max_hops is always respected."""
    # Returns list of dict records: [{"val": ..., "ioc_type": ..., "confidence": ..., "source": ...}]
    # OR empty list [] on any error
```

### DuckPGQGraph.find_paths()
```python
# quantum_pathfinder.py:763
async def find_paths(
    self,
    start_nodes: List[str],
    target_nodes: List[str],
    max_steps: Optional[int] = None
) -> List[List[str]]:
    """Quantum random walk with amplitude amplification.
    Returns list of paths (each path is list of node ID strings).
    Raises RuntimeError if pathfinder not initialized."""
```

### DuckPGQGraph init constraints
- `max_nodes = min(self.quantum_max_nodes, 5000)` (config.py)
- M1 RAM: 1000 nodes per traversal (audit)

### DuckPGQGraph.find_connected_batch()
```python
# quantum_pathfinder.py:1311
def find_connected_batch(self, values: list[str], max_hops: int = 2) -> dict[str, list[dict]]:
    """P1-1 batch optimization for N+1 query elimination."""
```

## sprint_seeds.lmdb Schema

| Field | Value |
|-------|-------|
| Path | `LMDB_ROOT / "sprint_seeds.lmdb"` = `~/.hledac/lmdb/sprint_seeds.lmdb` |
| Max map size | 256 MB |
| Key pattern | `b"seeds:{sprint_id}"` (UTF-8 encoded) |
| Value | `orjson.dumps(list[str])` — list of IOC value strings |
| Sync API | `sync_save_sprint_seeds(sprint_id, seeds)`, `sync_load_sprint_seeds(sprint_id)` |
| Async API | `async_save_sprint_seeds(sprint_id, seeds)`, `async_load_sprint_seeds(sprint_id)` |

## Changes Applied

### 1. runtime/sprint_scheduler.py
- **SprintSchedulerResult** field added (line ~1358):
  ```python
  quantum_path_seeds: list[str] = field(default_factory=list)
  ```
- **After `_accumulate_findings_to_graph()`** (lines 7578-7591):
  - Extracts `value` from findings (dict form)
  - Calls `_run_quantum_path_analysis(new_ioc_values)`
  - Stores result in `self._result.quantum_path_seeds`
  - Persists via `sync_save_sprint_seeds(sprint_id, quantum_seeds)`
- **New method** `_run_quantum_path_analysis()` (lines 7655-7680):
  ```python
  async def _run_quantum_path_analysis(self, new_ioc_values: list[str]) -> list[str]:
      """Post-sprint: find undiscovered connected IOCs via graph walk."""
      # Bound: first 10 IOCs, max 5 new vals per IOC, cap 50 total
      # Fail-soft: returns [] on any exception
  ```

### 2. knowledge/sprint_seeds_store.py (NEW)
- Canonical LMDB store for cross-sprint quantum pathfinder seeds
- `sync_save_sprint_seeds()` — sync write via `lmdb.open()` (export phase)
- `sync_load_sprint_seeds()` — sync read (seed generation phase)
- `async_save_sprint_seeds()` / `async_load_sprint_seeds()` — async via `AsyncLMDBKVStore`

### 3. export/sprint_exporter.py
- **In `_generate_next_sprint_seeds()`** after dedup (line ~1125):
  ```python
  # Sprint F214Q: Merge quantum pathfinder seeds with degree-centrality seeds
  from hledac.universal.knowledge.sprint_seeds_store import sync_load_sprint_seeds
  quantum_seeds = sync_load_sprint_seeds(sprint_id)
  if quantum_seeds:
      q_seed_dicts = [
          {"seed_type": "quantum_path", "query": q, "priority": 0.8,
           "source": "graph_quantum_pathfinder"}
          for q in quantum_seeds[:50]
      ]
      seeds = _dedup_seeds(q_seed_dicts + seeds)  # quantum first (higher fidelity)
      next_seeds_source = "quantum_pathfinder"
  ```
- Seeds prepended (quantum path seeds first) to preserve priority

### 4. knowledge/graph_service.py
- **upsert_ioc()** (line ~116): Validate `ioc_type` against `IOC_TYPES`:
  ```python
  from hledac.universal.knowledge.ioc_graph import IOC_TYPES as _VALID_IOC_TYPES
  if ioc_type not in _VALID_IOC_TYPES:
      logger.debug(f"[GraphService] unknown ioc_type={ioc_type!r}, falling back to 'unknown'")
      ioc_type = "unknown"
  ```
- **upsert_ioc_batch()** (line ~147): Same validation in loop before unique append
- `IOC_TYPES` = `frozenset(("cve", "ip", "hash_sha256", "hash_md5", "onion", "domain", "apt", "malware"))`

## Flow Summary

```
Sprint N findings
    ↓
_accumulate_findings_to_graph() → DuckPGQGraph upserts
    ↓
_run_quantum_path_analysis()    → find_connected() on first 10 IOCs
    ↓                           → discovers up to 50 undiscovered connected IOCs
sync_save_sprint_seeds()       → persists to sprint_seeds.lmdb
    ↓
Sprint N+1 seed generation
    ↓
_generate_next_sprint_seeds()
    ↓
sync_load_sprint_seeds()        ← loads Sprint N quantum seeds
    ↓                           ← merges with degree-centrality seeds
_next_sprint_seeds.json        → path-informed discovery, not just popularity
```

## Invariants
- `find_connected()` always returns `list[dict]` or `[]` (fail-soft)
- `find_paths()` is NOT called (unused — degree-centrality path only)
- M1 RAM budget: 10 IOCs × 5 vals × ~100 nodes = ~5000 nodes (within 5000 limit)
- All graph errors: fail-soft → empty list → sprint continues
- `quantum_path_seeds` field added to `SprintSchedulerResult` for telemetry