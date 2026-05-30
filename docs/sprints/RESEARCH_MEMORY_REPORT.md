# Sprint F224: Cross-Sprint Research Memory & Graph RAG Activation

## Executive Summary

Implemented cross-sprint epistemic memory and activated dormant Graph RAG for contextual enrichment.

## What Was Built

### Part A: Cross-Sprint Research Session Memory

**File:** `knowledge/research_memory.py` (NEW)

```python
class ResearchSessionMemory:
    """Persistent cross-sprint knowledge tracking."""
    
    async def record_sprint_outcome(...)  # After each sprint
    async def get_unexplored_angles(...)  # Before next sprint
    async def get_entity_history(...)      # Multi-sprint entity tracking
    async def detect_temporal_anomalies()   # Activity spikes/drops
```

**Key Dataclasses:**
- `EntityObservation` — single entity sighting
- `EntityHistory` — multi-sprint history with trend
- `TemporalAnomaly` — unusual activity patterns
- `UnexploredAngle` — query sub-directions to pursue

**DuckDB Tables Created:**
- `research_sessions` — sprint outcome records
- `entity_observations` — entity sighting history

### Part B: Graph RAG Activation

**File:** `runtime/sprint_scheduler.py` (MODIFIED)

Added `_run_graph_rag_context_sidecar()`:
- Runs BEFORE first cycle (pre-cycle enrichment)
- Gate: `HLEDAC_ENABLE_GRAPH_RAG=1` + RAM check < 5.0GB
- Uses `GraphRAGOrchestrator.multi_hop_search(query, hops=2)`
- Injects findings as `source_type="context_seed"`

**New Field in SprintSchedulerResult:**
- `graph_rag_context_count: int = 0`

### Part D: DuckDB dht_metadata Table

**File:** `knowledge/duckdb_store.py` (MODIFIED)

Added schema:
```sql
CREATE TABLE IF NOT EXISTS dht_metadata (
    infohash TEXT PRIMARY KEY,
    name TEXT,
    files_json TEXT,
    size_bytes BIGINT,
    first_seen DOUBLE,
    last_seen DOUBLE,
    peer_count INT,
    sources_json TEXT
)
```

Added method:
```python
async def async_ingest_dht_metadata(metadata: list[dict]) -> int
```

### Part E: LanceDB MRL Dimension

**Status:** Already correct (768→256 migration done in Sprint 77)
- `lancedb_store.py` line 157: `self._embedding_dim = 256`
- `self._current_mrl_dim = 256` at line 198

## Test Coverage

**File:** `tests/probe_f224_research_memory.py` (NEW)

```
tests/probe_f224_research_memory.py::test_research_memory_singleton PASSED
tests/probe_f224_research_memory.py::test_record_outcome_returns_id PASSED
tests/probe_f224_research_memory.py::test_get_unexplored_returns_list PASSED
tests/probe_f224_research_memory.py::test_get_entity_history_not_found PASSED
tests/probe_f224_research_memory.py::test_detect_temporal_anomalies_empty PASSED
tests/probe_f224_research_memory.py::test_dht_metadata_method_exists PASSED
tests/probe_f224_research_memory.py::test_dht_metadata_returns_int PASSED
tests/probe_f224_research_memory.py::test_graph_rag_context_sidecar_method_exists PASSED
tests/probe_f224_research_memory.py::test_graph_rag_context_count_in_result PASSED
tests/probe_f224_research_memory.py::test_full_research_memory_flow PASSED

10/10 PASSED
```

## Files Changed

| File | Change |
|------|--------|
| `knowledge/research_memory.py` | NEW — ResearchSessionMemory class |
| `knowledge/duckdb_store.py` | ADDED dht_metadata schema + async_ingest_dht_metadata() |
| `runtime/sprint_scheduler.py` | ADDED _run_graph_rag_context_sidecar() + graph_rag_context_count |
| `tests/probe_f224_research_memory.py` | NEW — 10 probe tests |
| `RESEARCH_MEMORY_REPORT.md` | This report |

## GHOST_INVARIANTS Verified

| Invariant | Test |
|-----------|------|
| DuckDB table CREATE IF NOT EXISTS | test_dht_metadata_method_exists |
| async_ingest_dht_metadata returns int | test_dht_metadata_returns_int |
| ResearchSessionMemory singleton | test_research_memory_singleton |
| record_sprint_outcome returns session_id | test_record_outcome_returns_id |
| get_unexplored_angles returns list | test_get_unexplored_returns_list |
| Graph RAG sidecar method exists | test_graph_rag_context_sidecar_method_exists |
| graph_rag_context_count in result | test_graph_rag_context_count_in_result |

## Usage

### Enable Graph RAG Context

```bash
export HLEDAC_ENABLE_GRAPH_RAG=1
```

### Record Sprint Outcome (after sprint)

```python
from hledac.universal.knowledge.research_memory import ResearchSessionMemory

mem = ResearchSessionMemory.get_instance()
if mem:
    await mem.record_sprint_outcome(
        sprint_id="sprint_001",
        query="target research query",
        findings=[canonical_finding1, canonical_finding2],
        gaps=[gap1, gap2]  # from EpistemicGapDetector
    )
```

### Get Hints for Next Sprint

```python
hints = await mem.get_next_sprint_hints(
    query="target research query",
    current_sprint_id="sprint_002"
)
# Returns: suggested_angles, temporal_anomalies, source_suggestions
```

### Ingest DHT Metadata

```python
from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

store = DuckDBShadowStore()
count = await store.async_ingest_dht_metadata([
    {"infohash": "abc123", "name": "file.torrent", "size_bytes": 1024}
])
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   Sprint Lifecycle                          │
├─────────────────────────────────────────────────────────────┤
│  Pre-Cycle: _run_graph_rag_context_sidecar()             │
│  ├── GraphRAGOrchestrator.multi_hop_search(query, hops=2) │
│  └── Injects context_seed findings into sprint             │
│                                                              │
│  Post-Sprint: record_sprint_outcome()                      │
│  ├── Extracts entities from findings                      │
│  ├── Analyzes source patterns                              │
│  ├── Detects temporal anomalies                           │
│  └── Persists to DuckDB research_sessions                 │
├─────────────────────────────────────────────────────────────┤
│                   DuckDB Storage                            │
│  ├── research_sessions (sprint outcomes)                  │
│  ├── entity_observations (multi-sprint history)          │
│  └── dht_metadata (DHT torrent discoveries)              │
└─────────────────────────────────────────────────────────────┘
```

## Memory Budget

- ResearchSessionMemory: Lazy singleton, no persistent connection
- DuckDB: Shared via duckdb module (not DuckDBShadowStore)
- Max entities per episode: 500 (MAX_EPISODE_ENTITIES)
- Max unexplored angles: 20 (MAX_UNEXPLORED_ANGLES)
- Max temporal anomalies: 100 (MAX_TEMPORAL_ANOMALIES)

## Commit

```bash
git add knowledge/research_memory.py knowledge/duckdb_store.py \
      runtime/sprint_scheduler.py tests/probe_f224_research_memory.py \
      RESEARCH_MEMORY_REPORT.md
git commit -m "feat: Sprint F224 cross-sprint research memory and Graph RAG activation"
```
