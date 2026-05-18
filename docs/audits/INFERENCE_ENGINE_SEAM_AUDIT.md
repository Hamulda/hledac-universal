# InferenceEngine Seam Audit — F228G

**Date**: 2026-05-18
**File**: `brain/inference_engine.py` (2382 lines, 83KB)
**Churn**: 99.9th %ile (hotspot)

## 1. Class Map

| Class | Line | Responsibility |
|-------|------|----------------|
| `Evidence` | 58 | Single fact with confidence, timestamp, metadata |
| `InferenceStep` | 88 | Single step in an inference chain |
| `Hypothesis` | 110 | Abductive reasoning result: observations → explanations |
| `ResolvedEntity` | 153 | Probabilistic entity resolution result |
| `InferenceRule` | 179 | OSINT rule: co-location, temporal proximity, stylometry |
| `InferenceType` | 196 | Enum: ABDUCTIVE, DEDUCTIVE, BAYESIAN, INDIRECT |
| `HopStep` | 211 | Single hop in multi-hop path |
| `MultiHopPath` | 250 | Path from start→end entity via hops |
| **`InferenceEngine`** | 366 | Main orchestrator: evidence storage, abductive reasoning, entity resolution, streaming |
| **`MultiHopReasoner`** | 1881 | BFS multi-hop path finder (nested class, stateless except inference_engine ref) |

## 2. InferenceEngine Public API

| Method | Line | Description |
|--------|------|-------------|
| `add_evidence` | 692 | Add single Evidence, bounded LRU |
| `add_evidence_batch` | 715 | Add multiple Evidence items |
| `abductive_reasoning` | 766 | Given observations, find best hypotheses |
| `resolve_entity` | ~850 | Probabilistic entity resolution |
| `update_belief` | ~900 | Bayesian belief updating |
| `run_inference_chain` | ~1000 | Multi-step inference chain |
| `stream_process` | ~1100 | Streaming inference |
| `resolve_entities_batch` | ~1200 | Batch entity resolution |
| `_run_mlx_similarity` | ~1400 | MLX similarity computation |
| `_build_hypothesis_rankings` | ~1500 | Hypothesis ranking |
| `get_inference_paths` | ~1614 | Multi-hop path finding via MultiHopReasoner |

## 3. Caller Map

| Caller | File | Notes |
|--------|------|-------|
| `legacy/autonomous_orchestrator.py:19180` | InferenceEngine() | Facade, __init__ only |
| `legacy/autonomous_orchestrator.py:23124-23126` | InferenceEngine() + MultiHopReasoner() | Legacy orchestrator |
| `tests/test_autonomous_orchestrator.py:9067,9079,15206,15229` | InferenceEngine() | Unit tests |

**No production callers found in `brain/` coordinators or `pipeline/`.** InferenceEngine is not wired into SprintScheduler, FetchCoordinator, or any active pipeline component.

## 4. MultiHopReasoner Analysis — First Seam Candidate

### Dependency Profile
```
MultiHopReasoner.__init__(inference_engine)
  └── inference_engine._evidence: OrderedDict[str, Evidence]
  └── inference_engine._evidence_graph: OrderedDict[str, Set[str]]
  └── inference_engine._inference_rules: List[InferenceRule]
  └── inference_engine.MAX_BFS_QUEUE, MAX_BFS_DEPTH
```

**No GPU/model dependency.** Only reads from in-memory OrderedDict structures.

### What MultiHopReasoner does
1. `_bfs_with_depth()` — BFS path finding, confidence pruning, cycle detection
2. `_find_evidence_for_entity()` — reads `_evidence` OrderedDict
3. `_get_entity_neighbors()` — reads `_evidence_graph`
4. `_get_evidence_for_relation()` — reads `_evidence`
5. `rank_paths()` — sorts paths by confidence

### What it does NOT touch
- No MLX, no GPU
- No async (only `reason()` is async, BFS is sync)
- No network, no LMDB, no DuckDB
- No model loading

### BFS bounds (already M1-safe)
- `MAX_BFS_QUEUE = 1000` — bounded via `deque(maxlen=...)`
- `MAX_BFS_DEPTH = 10` — enforced in `_bfs_with_depth()`
- `max_paths` — early termination counter

## 5. Recommendation: Extract MultiHopReasoner

**Seam**: Create `brain/multi_hop_reasoner.py` as first extraction.

**Rationale**:
- Stateless except for `inference_engine._evidence` reference (data-only seam)
- Already follows single-responsibility: BFS path finding
- No GPU/model coupling — pure Python with bounded collections
- Easy to test with fake `_evidence` OrderedDict
- Could become a first-class citizen for graph traversal in sprint pipeline

**Steps**:
1. Copy `MultiHopReasoner` class to new file `brain/multi_hop_reasoner.py`
2. Add `from brain.inference_engine import InferenceEngine` ref for type hint
3. Create thin `InferenceEvidenceAdapter` protocol for `_evidence` access
4. Add characterization tests (no model, fake data only)
5. Run existing tests to verify no regression

## 6. What NOT to Extract Yet

| Component | Reason |
|-----------|--------|
| `InferenceEngine` | God object — mixes evidence storage, abductive reasoning, entity resolution, MLX, streaming. Too early. |
| `Evidence`/`Hypothesis` dataclasses | Tightly coupled to InferenceEngine internals |
| `abductive_reasoning()` | Depends on `_inference_rules` + `_run_mlx_similarity()` |

## 7. Risk Assessment

| Aspect | Rating | Notes |
|--------|--------|-------|
| Extraction complexity | LOW | MultiHopReasoner is self-contained |
| Test regression risk | LOW | No production callers in active pipeline |
| M1 memory risk | NONE | Pure Python, bounded collections |
| GPU coupling | NONE | No MLX in MultiHopReasoner |

## 8. Next Steps

1. Create `docs/audits/INFERENCE_ENGINE_SEAM_AUDIT.md` (this file)
2. Create `tests/test_multi_hop_reasoner.py` — characterization tests only
3. Extract MultiHopReasoner to `brain/multi_hop_reasoner.py` in follow-up sprint